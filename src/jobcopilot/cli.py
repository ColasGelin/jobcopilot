"""Command-line entry point: `jobcopilot run`, `serve`, `cv`, `agent`, `import-legacy`, `install-schedule`, `uninstall-schedule`, `eval run|status|promote`."""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import typer
import uvicorn

from jobcopilot import schedule
from jobcopilot.config import load_profile, load_settings
from jobcopilot.daemon import loop
from jobcopilot.db import DB
from jobcopilot.legacy import import_legacy
from jobcopilot.llm import ClaudeCodeClient
from jobcopilot.pipeline import run_pipeline
from jobcopilot.sources import build_sources, http_client

app = typer.Typer(help="Local job-search copilot.", no_args_is_help=True)


@app.callback()
def main(ctx: typer.Context, home: Path = typer.Option(Path.cwd(), help="Folder with config.yaml, profile.yaml and data.")):
    ctx.obj = home.expanduser().resolve()


def make_preparer(settings, profile, db, llm, client):
    from jobcopilot.apply.fetch import fetch_job
    from jobcopilot.apply.prepare import Preparer
    from jobcopilot.apply.reader import page_text_in_browser, read_form
    from jobcopilot.apply.resolve import find_on_ats
    from jobcopilot.cv.bank import load_bank

    return Preparer(
        db=db, settings=settings, profile=profile, llm=llm,
        read=lambda job: read_form(job.url, job.company, settings.watchlist, client),
        bank_loader=lambda: load_bank(settings.cv_path),
        fetch=lambda url: fetch_job(url, settings.watchlist, client, llm, settings.llm.score_model, page_text_in_browser),
        resolve=lambda job: find_on_ats(job, client),
    )


def _preparer_factory(home: Path, settings):
    """For the dashboard's "Prepare now": a Preparer per request (SQLite connections stay in their thread)."""
    profile_path = home / "profile.yaml"
    if not profile_path.exists():
        return None

    def factory():
        return make_preparer(settings, load_profile(profile_path), DB(settings.db_path),
                             ClaudeCodeClient(timeout_s=settings.llm.timeout_s), http_client())

    return factory


def _prepare_pending(settings, profile, db, llm, client) -> None:
    try:
        done = make_preparer(settings, profile, db, llm, client).prepare_pending(datetime.now().astimezone())
        typer.echo(f"{done} applications prepared.")
    except Exception as e:  # the run itself succeeded; preparing is extra
        typer.echo(f"Preparing applications failed: {e!r}")


def _run(home: Path) -> int:
    """One full run. Returns the process exit code."""
    profile_path = home / "profile.yaml"
    if not profile_path.exists():
        typer.echo(f"Missing {profile_path}. Copy profile.example.yaml to profile.yaml and edit it.")
        return 1
    settings = load_settings(home / "config.yaml")
    profile = load_profile(profile_path)
    db = DB(settings.db_path)
    with http_client() as client:
        sources = build_sources(settings.sources, client, settings)
        llm = ClaudeCodeClient(timeout_s=settings.llm.timeout_s)
        result, report = run_pipeline(settings, profile, db, sources, llm, datetime.now().astimezone())
        if not result.error and settings.prepare.enabled:
            _prepare_pending(settings, profile, db, llm, client)
    if result.error:
        typer.echo(f"Run failed: {result.error}")
        return 1
    typer.echo(f"{result.new} new jobs, {result.kept} passed filters, {result.drafted} drafted.")
    if result.failed_sources:
        typer.echo(f"Failed sources: {', '.join(result.failed_sources)}")
    typer.echo(f"Report: {report}")
    return 0


def _check_mail(home: Path) -> int:
    """Read the inbox once and move applications forward. Returns the process exit code."""
    from jobcopilot.mail import check_mail, fetch

    settings = load_settings(home / "config.yaml")
    mail, now = settings.mail, datetime.now().astimezone()
    if not mail.enabled:
        typer.echo("Mail checking is off. Set mail.enabled and mail.user in config.yaml.")
        return 1
    db = DB(settings.db_path)
    password = os.environ.get("JOBCOPILOT_IMAP_PASSWORD", "")
    if not mail.user or not password:
        db.set_mail_check(now, "mail.user or JOBCOPILOT_IMAP_PASSWORD is not set")
        typer.echo("Set mail.user in config.yaml and JOBCOPILOT_IMAP_PASSWORD in .env.")
        return 1
    try:
        mails = fetch(mail.host, mail.user, password, mail.folder, (now - timedelta(days=mail.lookback_days)).date(),
                      db.processed_mail_ids(), now)
        changes = check_mail(db, mails, ClaudeCodeClient(timeout_s=settings.llm.timeout_s), settings.llm.score_model, now)
    except Exception as e:  # shown on the dashboard; the password never appears in imaplib errors
        db.set_mail_check(now, f"{type(e).__name__}: {e}"[:300])
        typer.echo(f"Mail check failed: {e!r}")
        return 1
    db.set_mail_check(now, None)
    for c in changes:
        typer.echo(f"{c['company']} · {c['title']}: {c['from_status']} → {c['to_status']} ({c['subject']})")
    typer.echo(f"Mail checked: {len(mails)} new emails, {len(changes)} status changes.")
    return 0


@app.command()
def mail(ctx: typer.Context):
    """Read the inbox and move applications forward (received, interview, offer, rejected)."""
    raise typer.Exit(_check_mail(ctx.obj))


@app.command()
def run(ctx: typer.Context):
    """Collect, filter, score and draft today's jobs."""
    raise typer.Exit(_run(ctx.obj))


# --- evaluation of the AI features --------------------------------------------------
eval_app = typer.Typer(help="Evaluate the AI features against a test set and a saved baseline.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


def _eval_store(home: Path):
    from jobcopilot.evals.store import EvalStore

    return EvalStore(load_settings(home / "config.yaml").data_dir / "evals")


def _eval_run(home: Path, tasks: list[str], limit: int | None):
    from jobcopilot.evals.runner import run_evals

    settings = load_settings(home / "config.yaml")
    return run_evals(tasks, profile=load_profile(home / "profile.yaml"), settings=settings, db=DB(settings.db_path),
                     llm=ClaudeCodeClient(timeout_s=settings.llm.timeout_s), now=datetime.now().astimezone(), limit=limit)


def _print_report(r: dict) -> None:
    typer.echo(f"{r['verdict'].replace('_', ' ').upper()}  (run {r['run'].id}" + (f", baseline {r['baseline'].id})" if r["baseline"] else ")"))
    for row in r["rows"]:
        if row["error"]:
            typer.echo(f"  {row['name']}: skipped. {row['error']}")
            continue
        change = "" if row["change"] is None else f"  {row['change'] * 100:+.0f} pts vs baseline ({row['verdict']})"
        typer.echo(f"  {row['name']}: {row['value'] * 100:.0f}% of {row['n']} cases "
                   f"[{row['ci_low'] * 100:.0f}-{row['ci_high'] * 100:.0f}%]{change}  {row['calls']} calls, {row['seconds']:.0f}s")
    for f in r["failures"][:15]:
        c = f["case"]
        typer.echo(f"  FAIL {f['task']}: {c.label}: expected {c.expected}, got {c.actual}")
    typer.echo(r["next"])


@eval_app.command("run")
def eval_run(
    ctx: typer.Context,
    tasks: str = typer.Option("scoring,decisions,cv", help="Comma-separated: scoring, decisions, cv."),
    limit: int = typer.Option(None, help="Cases per task, for a quick check."),
    gate: bool = typer.Option(False, help="Exit with code 1 when a task got clearly worse than the baseline."),
):
    """Run the evaluation, save it, and compare it with the baseline."""
    from jobcopilot.evals.store import report

    store = _eval_store(ctx.obj)
    result = _eval_run(ctx.obj, [t.strip() for t in tasks.split(",") if t.strip()], limit)
    store.save(result)
    r = report(result, store.baseline())
    _print_report(r)
    if gate and r["verdict"] == "blocked":
        raise typer.Exit(1)


@eval_app.command("status")
def eval_status(ctx: typer.Context):
    """Show the latest evaluation run against the baseline."""
    from jobcopilot.evals.store import report

    store = _eval_store(ctx.obj)
    latest = store.latest()
    if latest is None:
        typer.echo("No evaluation run yet. Start one with: jobcopilot eval run")
        return
    _print_report(report(latest, store.baseline()))


@eval_app.command("promote")
def eval_promote(ctx: typer.Context, run_id: str = typer.Argument(None, help="Run to promote; the latest when omitted.")):
    """Make a run the baseline that later runs are compared with."""
    store = _eval_store(ctx.obj)
    run_id = run_id or (store.latest().id if store.latest() else None)
    if run_id is None or store.load(run_id) is None:
        typer.echo("No such run.")
        raise typer.Exit(1)
    store.promote(run_id)
    typer.echo(f"Baseline is now run {run_id}.")


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as e:
        raise typer.BadParameter(f"expected HH:MM, got {value!r}") from e


@app.command()
def daemon(ctx: typer.Context, at: str = typer.Option("08:30", help="Daily run time (HH:MM, local time).")):
    """Keep running and do one run per day at or after --at (catches up after sleep). For containers."""
    run_at = _parse_time(at)
    home: Path = ctx.obj
    settings = load_settings(home / "config.yaml")

    def state():
        db = DB(settings.db_path)
        return db.last_success_day(), db.last_attempt_at()

    def run_once():
        try:
            _run(home)
        except Exception as e:  # keep the daemon alive; the run row already records the error
            typer.echo(f"Run crashed: {e!r}")

    def mail_once():
        try:
            _check_mail(home)
        except Exception as e:  # keep the daemon alive
            typer.echo(f"Mail check crashed: {e!r}")

    every = timedelta(minutes=settings.mail.check_every_minutes) if settings.mail.enabled else None
    typer.echo(f"jobcopilot daemon: daily run at {run_at.strftime('%H:%M')} (checks every minute)"
               + (f", inbox every {settings.mail.check_every_minutes} min" if every else ""))
    loop(run_once, state, run_at, periodic=mail_once if every else None, every=every)


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", help="Interface to bind. Keep 127.0.0.1 unless inside a container."),
    port: int = typer.Option(8077),
):
    """Start the dashboard."""
    from jobcopilot import web

    settings = load_settings(ctx.obj / "config.yaml")
    app_ = web.create_app(
        settings.db_path,
        min_score=settings.min_score,
        companies=settings.companies_to_check,
        cv_path=settings.cv_path,
        cvs_dir=settings.cvs_dir,
        llm_factory=lambda: ClaudeCodeClient(timeout_s=settings.llm.timeout_s),
        draft_model=settings.llm.draft_model,
        preparer_factory=_preparer_factory(ctx.obj, settings),
        evals_dir=settings.data_dir / "evals",
        eval_runner=(lambda tasks: _eval_run(ctx.obj, tasks, None)) if (ctx.obj / "profile.yaml").exists() else None,
    )
    uvicorn.run(app_, host=host, port=port)


def _write_pdf(path: Path, content):
    from jobcopilot.cv.render import render_pdf

    result = render_pdf(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.pdf)
    typer.echo(f"CV: {path}")
    if result.removed:
        typer.echo("Shortened to fit one page, removed: " + "; ".join(result.removed))
    return result


@app.command()
def cv(ctx: typer.Context, job: str = typer.Option(None, help="Job id: tailor the CV to this job with Claude.")):
    """Render the main CV from cv.yaml to cvs/cv-main.pdf, or a CV tailored to one job (--job)."""
    from jobcopilot.cv.bank import BankError, load_bank
    from jobcopilot.cv.content import main_content
    from jobcopilot.cv.tailor import TAILOR_PROMPT_VERSION, TailorError, cv_filename, tailor_cv
    from jobcopilot.llm import LLMError

    settings = load_settings(ctx.obj / "config.yaml")
    try:
        bank = load_bank(settings.cv_path)
    except BankError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    if job is None:
        _write_pdf(settings.cvs_dir / "cv-main.pdf", main_content(bank))
        return
    db = DB(settings.db_path)
    target = db.get_job(job)
    if target is None:
        typer.echo(f"Unknown job {job!r}.")
        raise typer.Exit(1)
    match = db.match(job)
    llm = ClaudeCodeClient(timeout_s=settings.llm.timeout_s)
    try:
        content = tailor_cv(target, match.evaluation if match else None, bank, llm, settings.llm.draft_model)
    except TailorError as e:
        typer.echo("Tailoring failed; the answer broke these rules:\n- " + "\n- ".join(e.violations))
        raise typer.Exit(1)
    except LLMError as e:
        typer.echo(f"Claude is unavailable: {e}")
        raise typer.Exit(1)
    result = _write_pdf(settings.cvs_dir / cv_filename(target), content)
    db.save_tailored_cv(job, result.content, result.pages, settings.llm.draft_model, TAILOR_PROMPT_VERSION)


@app.command()
def agent(
    dashboard: str = typer.Option("http://localhost:8077", help="Dashboard URL (through the SSH tunnel)."),
    install: bool = typer.Option(False, "--install", help="Start the agent at login (macOS LaunchAgent)."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove the LaunchAgent."),
):
    """On the Mac: fill application forms in a visible browser when you click Fill form (never submits)."""
    from jobcopilot.agent import launchd, loop

    if install:
        repo = Path(__file__).resolve().parents[2]
        path = launchd.install(shutil.which("uv") or "uv", repo, dashboard)
        typer.echo(f"Agent installed ({path}); it starts at login. Log: {launchd.LOG}")
    elif uninstall:
        launchd.uninstall()
        typer.echo("Agent removed.")
    else:
        loop.run_agent(dashboard)


@app.command("import-legacy")
def import_legacy_cmd(ctx: typer.Context):
    """Import seen.json and tracker.tsv from the earlier version."""
    settings = load_settings(ctx.obj / "config.yaml")
    typer.echo(f"Imported {import_legacy(DB(settings.db_path), ctx.obj)} jobs.")


@app.command("install-schedule")
def install_schedule(ctx: typer.Context, hour: int = 8, minute: int = 30):
    """Run `jobcopilot run` every day via launchd (macOS)."""
    exe = shutil.which("jobcopilot") or str(Path(sys.executable).parent / "jobcopilot")
    path = schedule.install(exe, ctx.obj, hour, minute)
    typer.echo(f"Scheduled daily at {hour:02d}:{minute:02d} ({path}).")


@app.command("uninstall-schedule")
def uninstall_schedule():
    """Remove the launchd job."""
    schedule.uninstall()
    typer.echo("Schedule removed.")
