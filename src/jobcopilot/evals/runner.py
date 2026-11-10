"""Run the evaluation tasks and time them."""

from __future__ import annotations

import time
from datetime import datetime

from jobcopilot.config import Profile, Settings
from jobcopilot.cv.bank import BankError, load_bank
from jobcopilot.cv.tailor import TAILOR_PROMPT_VERSION
from jobcopilot.db import DB
from jobcopilot.evals import tasks
from jobcopilot.evals.models import Case, EvalRun, TaskResult
from jobcopilot.evals.stats import mean_ci
from jobcopilot.llm import LLMClient
from jobcopilot.scoring import SCORE_PROMPT_VERSION

TASKS = ("scoring", "decisions", "cv")
CV_CASES = 5  # each one is a slow drafting call


class _Timed:
    """Counts the LLM calls of a task and the time they take."""

    def __init__(self, llm: LLMClient):
        self.llm, self.calls, self.seconds = llm, 0, 0.0

    def complete_json(self, prompt: str, schema: dict, model: str) -> dict:
        start = time.monotonic()
        self.calls += 1
        try:
            return self.llm.complete_json(prompt, schema, model)
        finally:
            self.seconds += time.monotonic() - start


def run_evals(names: list[str], *, profile: Profile, settings: Settings, db: DB, llm: LLMClient, now: datetime,
              cases: list[Case] | None = None, limit: int | None = None) -> EvalRun:
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        raise ValueError(f"unknown task {unknown[0]!r}; expected one of {TASKS}")
    if cases is None:
        cases = tasks.load_cases(settings.data_dir / "evals")
    score_model, draft_model = settings.llm.score_model, settings.llm.draft_model
    run = EvalRun(id=now.strftime("%Y%m%d-%H%M%S"), started_at=now)
    for name in names:
        timed = _Timed(llm)
        result = TaskResult(task=name, metric=tasks.METRICS[name])
        if name == "scoring":
            run.models[name], run.prompts[name] = score_model, SCORE_PROMPT_VERSION
            result.cases = tasks.scoring(tasks.take(cases, limit), profile, timed, score_model)
        elif name == "decisions":
            run.models[name], run.prompts[name] = score_model, SCORE_PROMPT_VERSION
            own = tasks.decision_cases(db)
            if len({c.expect["applied"] for c in own}) < 2:
                result.error = "Needs at least one applied and one skipped job; it fills up as you use the dashboard."
            else:
                result.cases = tasks.decisions(tasks.take(own, limit), profile, timed, score_model)
        else:
            run.models[name], run.prompts[name] = draft_model, TAILOR_PROMPT_VERSION
            try:
                bank = load_bank(settings.cv_path)
                result.cases = tasks.cv([c for c in cases if "cv" in c.tags][: limit or CV_CASES], bank, timed, draft_model)
            except BankError as e:
                result.error = f"Experience bank problem: {e}"
        result.n, result.calls, result.seconds = len(result.cases), timed.calls, round(timed.seconds, 1)
        result.value, result.ci_low, result.ci_high = mean_ci([c.score for c in result.cases])
        run.tasks.append(result)
    return run
