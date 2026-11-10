"""What the dashboard works out from the jobs: the next step of an offer and the numbers on the Dashboard page."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta

from jobcopilot.models import Job
from jobcopilot.report import SOURCE_NAMES
from jobcopilot.text import normalize

TODAY_FILTERS = (("all", "All"), ("strong", "Strong 80+"), ("remote", "Remote"), ("ready", "Ready"))
STRONG_SCORE = 80
DAYS_SHOWN = 14
TODO_SHOWN = 8
SOURCES_SHOWN = 6

_CLOSED = ("applied", "interview", "offer", "rejected", "skipped", "unavailable")
_REACHED = ("applied", "interview", "offer")
_SCORE_BANDS = (("0–39", 0, 39), ("40–59", 40, 59), ("60–69", 60, 69), ("70–79", 70, 79), ("80–100", 80, 100))
_TODO_ORDER = {"ready": 0, "follow_up": 1, "needs_you": 2}


_BOARDS = ("linkedin", "indeed")  # their remote flag comes from our search filter, not from the posting
_REMOTE_WORDS = ("remote", "remoto", "remota", "teletrabajo", "work from home", "desde casa", "wfh")


def remote_unconfirmed(job: Job) -> bool:
    """A board offer counted as remote although its own text never says so: it may be hybrid or on-site."""
    if job.source not in _BOARDS or not job.remote:
        return False
    text = normalize(f"{job.title} {job.location} {job.description}")
    return not any(word in text for word in _REMOTE_WORDS)


RUN_LATE_AFTER = time(10, 0)  # the run starts at 08:30 and takes a few minutes


def run_warning(last_success_day: date | None, now: datetime) -> str | None:
    """Why the daily run looks missing, or None. A missing run usually means the server or its containers are down."""
    if last_success_day is None:
        return "No daily run has finished yet."
    late = (now.date() - last_success_day).days
    if late >= 2:
        return f"No daily run since {last_success_day.strftime('%d %b')}."
    if late == 1 and now.time() >= RUN_LATE_AFTER:
        return f"No daily run today yet (last one {last_success_day.strftime('%d %b')})."
    return None


def next_step(status: str, prep: str | None) -> str | None:
    """The one action an offer is waiting for: prepare, review, apply, or None."""
    if status in _CLOSED:
        return None
    if prep is None:
        return "prepare"
    return {"ready": "review", "needs_you": "review", "filled": "apply"}.get(prep)


def steps(status: str, has_cv: bool, prep: str | None, has_cover: bool) -> list[dict]:
    applied = status in _REACHED or prep == "applied"
    application = "done" if applied or prep in ("ready", "filled") else "todo"
    if prep == "needs_you" and status not in _CLOSED:
        application = "attention"
    return [
        {"label": "CV", "state": "done" if has_cv else "todo"},
        {"label": "Application", "state": application},
        {"label": "Cover letter", "state": "done" if has_cover else "todo"},
        {"label": "Applied", "state": "done" if applied else "todo"},
    ]


def _stage(row: dict, min_score: int) -> int:
    """How far an offer got, so that each funnel stage holds everything in the next one."""
    if row["status"] == "offer":
        return 5
    if row["status"] == "interview":
        return 4
    if row["status"] in _REACHED or row["applied_at"]:
        return 3
    if row["prep"] in ("ready", "filled", "applied"):
        return 2
    return 1 if row["score"] >= min_score else 0


def _funnel(rows: list[dict], min_score: int) -> list[dict]:
    stages = [_stage(r, min_score) for r in rows]
    labels = ("Found", "Matched", "Prepared", "Applied", "Interview", "Offer")
    return [{"label": label, "count": sum(1 for s in stages if s >= i)} for i, label in enumerate(labels)]


def _days(rows: list[dict], today: date) -> list[dict]:
    counts = Counter(r["day"] for r in rows)
    shown = [today - timedelta(days=n) for n in range(DAYS_SHOWN - 1, -1, -1)]
    return [{"day": d.isoformat(), "label": d.strftime("%d %b"), "count": counts[d.isoformat()]} for d in shown]


def _sources(rows: list[dict]) -> list[dict]:
    counts = Counter(SOURCE_NAMES.get(r["source"], r["source"]) for r in rows).most_common()
    top = [{"label": label, "count": count} for label, count in counts[:SOURCES_SHOWN]]
    rest = sum(count for _, count in counts[SOURCES_SHOWN:])
    return top + [{"label": "Other", "count": rest}] if rest else top


def _todo(rows: list[dict], follow_ups: set[str]) -> dict:
    items = []
    for r in rows:
        application = f"/jobs/{r['id']}/application"
        if r["id"] in follow_ups:
            kind, what, href = "follow_up", "Follow up", f"/today?day={r['day']}&all=1#job-{r['id']}"
        elif r["status"] in _CLOSED:
            continue
        elif r["prep"] in ("ready", "filled"):
            kind, what, href = "ready", "Application ready", application
        elif r["prep"] == "needs_you":
            kind, what, href = "needs_you", f"Needs you: {r['reason']}", application
        else:
            continue
        items.append({"id": r["id"], "company": r["company"], "title": r["title"], "score": r["score"],
                      "kind": kind, "what": what, "href": href})
    items.sort(key=lambda t: (_TODO_ORDER[t["kind"]], -t["score"]))
    return {"items": items[:TODO_SHOWN], "total": len(items)}


def dashboard(rows: list[dict], today: date, min_score: int, follow_ups: set[str]) -> dict:
    scores = [{"label": label, "count": sum(1 for r in rows if low <= r["score"] <= high)} for label, low, high in _SCORE_BANDS]
    funnel = _funnel(rows, min_score)
    reached = {f["label"]: f["count"] for f in funnel}
    return {
        "kpis": {
            "found": len(rows),
            "strong": sum(1 for r in rows if r["score"] >= STRONG_SCORE),
            "ready": sum(1 for r in rows if r["prep"] in ("ready", "filled") and r["status"] not in _CLOSED),
            "applied": reached["Applied"],
            "interviews": reached["Interview"],
        },
        "funnel": funnel,
        "days": _days(rows, today),
        "scores": scores,
        "sources": _sources(rows),
        "todo": _todo(rows, follow_ups),
    }
