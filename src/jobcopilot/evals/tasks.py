"""The evaluated features. Each task turns cases into per-case results; its metric is their mean score."""

from __future__ import annotations

import json
from importlib import resources
from itertools import zip_longest
from pathlib import Path

from pydantic import ValidationError

from jobcopilot.config import Profile
from jobcopilot.cv.bank import Bank
from jobcopilot.cv.guard import TailorResponse, check
from jobcopilot.cv.tailor import SCHEMA as TAILOR_SCHEMA
from jobcopilot.cv.tailor import _render as render_tailor
from jobcopilot.db import DB
from jobcopilot.evals.models import Case, CaseResult
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.scoring import score_jobs

METRICS = {"scoring": "in_band", "decisions": "ranks_applied_higher", "cv": "clean_first_try"}
DECISIONS_SHOWN = 40
_APPLIED = ("applied", "interview", "offer", "rejected")  # rejected by the company: the candidate still chose it


def load_cases(folder: Path | None) -> list[Case]:
    """Scoring cases: `scoring.jsonl` in the data folder's `evals/` when there is one, else the packaged set."""
    custom = folder / "scoring.jsonl" if folder else None
    if custom is not None and custom.exists():
        text = custom.read_text(encoding="utf-8")
    else:
        text = resources.files("jobcopilot.evals").joinpath("datasets/scoring.jsonl").read_text(encoding="utf-8")
    return [Case.model_validate_json(line) for line in text.splitlines() if line.strip()]


def take(cases: list[Case], limit: int | None) -> list[Case]:
    """The first `limit` cases, alternating expected fits and misfits so a short run still has both."""
    if limit is None or limit >= len(cases):
        return cases
    fits = [c for c in cases if "min" in c.expect or c.expect.get("applied") is True]
    rest = [c for c in cases if c not in fits]
    mixed = [c for pair in zip_longest(fits, rest) for c in pair if c is not None]
    return mixed[:limit]


def _scores(cases: list[Case], profile: Profile, llm: LLMClient, model: str) -> dict[str, tuple[int, str]]:
    """case id -> (score, gaps) for the cases the model scored."""
    by_job = {c.to_job().id: c.id for c in cases}
    try:
        evaluations, _ = score_jobs([c.to_job() for c in cases], profile, llm, model)
    except LLMUnavailable:
        raise
    except LLMError:
        evaluations = []
    return {by_job[e.job_id]: (e.score, e.gaps) for e in evaluations}


def scoring(cases: list[Case], profile: Profile, llm: LLMClient, model: str) -> list[CaseResult]:
    """Is each posting's score on the right side of its line: clear fits high, clear misfits low?"""
    got = _scores(cases, profile, llm, model)
    results = []
    for c in cases:
        expected = f"{c.expect['min']} or more" if "min" in c.expect else f"{c.expect['max']} or less"
        if c.id not in got:
            results.append(CaseResult(case_id=c.id, label=c.label, score=0.0, passed=False, expected=expected, actual="not scored"))
            continue
        score, gaps = got[c.id]
        ok = score >= c.expect["min"] if "min" in c.expect else score <= c.expect["max"]
        results.append(CaseResult(case_id=c.id, label=c.label, score=float(ok), passed=ok, expected=expected, actual=str(score), note=gaps))
    return results


def decision_cases(db: DB) -> list[Case]:
    """The candidate's own decisions as cases: jobs applied to, and jobs skipped (most recent first)."""
    rows = db.conn.execute(
        "SELECT job_id, status FROM applications WHERE status IN ('applied', 'interview', 'offer', 'rejected', 'skipped')"
        " ORDER BY updated_at DESC, job_id LIMIT ?", (DECISIONS_SHOWN,),
    ).fetchall()
    cases = []
    for r in rows:
        job = db.get_job(r["job_id"])
        if job is not None:
            cases.append(Case(id=job.id, task="decisions", source="decision", expect={"applied": r["status"] in _APPLIED},
                              job=job.model_dump(mode="json", exclude={"posted_at"})))
    return sorted(cases, key=lambda c: not c.expect["applied"])


def decisions(cases: list[Case], profile: Profile, llm: LLMClient, model: str) -> list[CaseResult]:
    """Does the score rank the jobs the candidate applied to above the ones they skipped? Each case scores the
    share of opposite decisions it is on the right side of (a tie counts half), so the mean is a ranking accuracy."""
    got = _scores(cases, profile, llm, model)
    applied = [got[c.id][0] for c in cases if c.expect["applied"] and c.id in got]
    skipped = [got[c.id][0] for c in cases if not c.expect["applied"] and c.id in got]
    results = []
    for c in cases:
        expected = "above the skipped jobs" if c.expect["applied"] else "below the applied jobs"
        if c.id not in got:
            results.append(CaseResult(case_id=c.id, label=c.label, score=0.0, passed=False, expected=expected, actual="not scored"))
            continue
        score, gaps = got[c.id]
        others = skipped if c.expect["applied"] else applied
        wins = sum(1.0 if (score > o) == c.expect["applied"] and score != o else 0.5 if score == o else 0.0 for o in others)
        share = wins / len(others) if others else 0.0
        results.append(CaseResult(case_id=c.id, label=c.label, score=share, passed=share >= 0.5, expected=expected,
                                  actual=f"{score} ({'applied' if c.expect['applied'] else 'skipped'})", note=gaps))
    return results


def cv(cases: list[Case], bank: Bank, llm: LLMClient, model: str) -> list[CaseResult]:
    """Does the tailored CV pass the fact guard on the first try, before any retry (nothing invented, rules kept)?"""
    results = []
    for c in cases:
        try:
            response = TailorResponse.model_validate(llm.complete_json(render_tailor(c.to_job(), None, bank), TAILOR_SCHEMA, model))
            problems = check(response, bank)
        except LLMUnavailable:
            raise
        except (LLMError, ValidationError) as e:
            problems = [f"no usable answer: {str(e)[:200]}"]
        results.append(CaseResult(case_id=c.id, label=c.label, score=float(not problems), passed=not problems,
                                  expected="no guard problem", actual="; ".join(problems)[:600] or "clean"))
    return results


def dump_cases(cases: list[Case]) -> str:
    return "".join(json.dumps(c.model_dump(), ensure_ascii=False) + "\n" for c in cases)
