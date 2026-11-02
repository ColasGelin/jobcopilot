"""Command-line entry point: `jobcopilot run`, `import-legacy`, `install-schedule`, `uninstall-schedule`."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, time
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
    if result.error:
        typer.echo(f"Run failed: {result.error}")
        return 1
    typer.echo(f"{result.new} new jobs, {result.kept} passed filters, {result.drafted} drafted.")
    if result.failed_sources:
        typer.echo(f"Failed sources: {', '.join(result.failed_sources)}")
    typer.echo(f"Report: {report}")
    return 0


@app.command()
def run(ctx: typer.Context):
    """Collect, filter, score and draft today's jobs."""
    raise typer.Exit(_run(ctx.obj))


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

    typer.echo(f"jobcopilot daemon: daily run at {run_at.strftime('%H:%M')} (checks every minute)")
    loop(run_once, state, run_at)


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", help="Interface to bind. Keep 127.0.0.1 unless inside a container."),
    port: int = typer.Option(8077),
):
    """Start the dashboard."""
    from jobcopilot import web

    settings = load_settings(ctx.obj / "config.yaml")
    uvicorn.run(web.create_app(settings.db_path, min_score=settings.min_score), host=host, port=port)


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
