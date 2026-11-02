"""Local dashboard: Today, Pipeline and Runs views over jobs.db."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates

from jobcopilot.db import DB
from jobcopilot.models import STATUSES, Run
from jobcopilot.report import SOURCE_NAMES

HERE = Path(__file__).parent


def duration(run: Run) -> str:
    if not run.finished_at:
        return "—"
    seconds = int((run.finished_at - run.started_at).total_seconds())
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def safe_url(url: str) -> str | None:
    """Only http(s) links are rendered; feed URLs like `javascript:` are shown as text."""
    return url if url.lower().startswith(("http://", "https://")) else None


def _shift(day: date, days: int) -> date | None:
    try:
        return day + timedelta(days=days)
    except OverflowError:
        return None


def now_local() -> datetime:
    return datetime.now().astimezone()


def create_app(
    db_path: Path,
    clock: Callable[[], datetime] = now_local,
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1"),
    min_score: int = 60,
) -> FastAPI:
    app = FastAPI(title="jobcopilot", docs_url=None, redoc_url=None, openapi_url=None)
    # No auth by design, so: refuse foreign Host headers (DNS rebinding) ...
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")  # autoescape is on for .html
    templates.env.globals["source_name"] = lambda s: SOURCE_NAMES.get(s, s)
    templates.env.globals["duration"] = duration
    templates.env.globals["safe_url"] = safe_url

    def db() -> DB:
        return DB(db_path)  # one connection per request; the daemon writes to the same file

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request, day: str | None = None, all: bool = False):
        store = db()
        notice = None
        parsed = None
        if day:
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                notice = f"Invalid date {day!r}; showing the latest matches."
        day = parsed or store.latest_match_day()
        matches = store.matches_for_day(day) if day else []
        hidden = 0 if all else sum(1 for m in matches if m.evaluation.score < min_score)
        if not all:
            matches = [m for m in matches if m.evaluation.score >= min_score]
        return templates.TemplateResponse(
            request,
            "today.html",
            {
                "day": day,
                "prev_day": _shift(day, -1) if day else None,
                "next_day": _shift(day, 1) if day else None,
                "notice": notice,
                "hidden": hidden,
                "min_score": min_score,
                "matches": matches,
                "follow_ups": store.follow_up_due_ids(clock()),
                "active": "today",
            },
        )

    @app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
    def set_status(request: Request, job_id: str, status: str = Form(...), compact: bool = False):
        # ... and require htmx's custom header, which a cross-site form cannot send (CSRF).
        if request.headers.get("hx-request") != "true":
            raise HTTPException(403, "requests must come from the dashboard")
        if status not in STATUSES:
            raise HTTPException(400, f"unknown status {status!r}")
        store = db()
        if store.match(job_id) is None:
            raise HTTPException(404, "unknown job")
        store.set_status(job_id, status, clock())
        return templates.TemplateResponse(
            request,
            "_card.html",
            {"m": store.match(job_id), "follow_ups": store.follow_up_due_ids(clock()), "compact": compact},
        )

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline(request: Request):
        store = db()
        groups = store.pipeline()
        return templates.TemplateResponse(
            request,
            "pipeline.html",
            {
                "groups": groups,
                "total": sum(len(v) for v in groups.values()),
                "follow_ups": store.follow_up_due_ids(clock()),
                "active": "pipeline",
            },
        )

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request):
        return templates.TemplateResponse(request, "runs.html", {"runs": db().list_runs(), "active": "runs"})

    return app
