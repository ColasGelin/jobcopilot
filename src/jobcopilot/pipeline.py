"""The daily run: collect → dedupe → filter → score → draft → store."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from jobcopilot.config import Profile, Settings
from jobcopilot.db import DB
from jobcopilot.drafting import draft_application
from jobcopilot.filters import apply_filters
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Draft, Evaluation, Job, Run
from jobcopilot.report import add_to_report, render_report
from jobcopilot.scoring import score_jobs
from jobcopilot.sources import Source


def _collect(sources: list[Source], since: datetime, run: Run) -> list[Job]:
    jobs: list[Job] = []
    for source in sources:
        try:
            jobs.extend(source.fetch(since))
        except Exception:  # one broken feed must not stop the others
            run.failed_sources.append(source.name)
        run.failed_sources.extend(getattr(source, "errors", []))  # partial failures, e.g. one watchlist company
    return jobs


def run_pipeline(
    settings: Settings, profile: Profile, db: DB, sources: list[Source], llm: LLMClient, now: datetime
) -> tuple[Run, Path | None]:
    run = Run(started_at=now)
    run_id = db.start_run(run)
    kept: list[Job] = []
    try:
        jobs = _collect(sources, now - timedelta(days=settings.lookback_days), run)
        run.fetched = len(jobs)
        fresh = db.filter_new(jobs)
        run.new = len(fresh)
        # Only jobs that pass the filters are stored, so a profile change re-evaluates the rest.
        passing = apply_filters(fresh, profile.hard_filters)
        run.kept = len(passing)
        # Newest first; anything over the cap is not stored, so the next run picks it up.
        passing.sort(key=lambda j: j.posted_at.timestamp() if j.posted_at else 0, reverse=True)
        kept = passing[: settings.max_scored_per_run]
        db.insert_jobs(kept, now)

        evaluations, failed = score_jobs(kept, profile, llm, settings.llm.score_model) if kept else ([], [])
        db.delete_jobs(failed)  # retried next run
        for e in evaluations:
            db.save_evaluation(e)
        run.scored = len(evaluations)

        by_id = {j.id: j for j in kept}
        best = sorted((e for e in evaluations if e.score >= settings.min_score), key=lambda e: e.score, reverse=True)
        items: list[tuple[Job, Evaluation, Draft | None]] = []
        for e in best[: settings.top_n]:
            job = by_id[e.job_id]
            try:
                draft = draft_application(job, e, profile, llm, settings.llm.draft_model)
                db.save_draft(draft)
                run.drafted += 1
            except LLMUnavailable:
                raise
            except LLMError:
                db.mark_llm_error(job.id)
                draft = None
            items.append((job, e, draft))

        content = render_report(now.date(), run, items, db.follow_ups_due(now), unscored=len(failed))
        path = add_to_report(settings.reports_dir, now.date(), content, has_items=bool(items))
    except LLMUnavailable as e:
        # Forget this run's new jobs so the next run picks them up again.
        db.delete_jobs([j.id for j in kept])
        run.error = str(e)
        path = None
    except Exception as e:
        run.error = repr(e)
        run.finished_at = datetime.now(now.tzinfo)
        db.finish_run(run_id, run)
        raise
    run.finished_at = datetime.now(now.tzinfo)
    db.finish_run(run_id, run)
    return run, path
