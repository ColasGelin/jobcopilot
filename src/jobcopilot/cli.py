"""Command-line entry point: `jobcopilot run`, `import-legacy`, `install-schedule`, `uninstall-schedule`."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import typer

from jobcopilot import schedule
from jobcopilot.config import load_profile, load_settings
from jobcopilot.db import DB
from jobcopilot.legacy import import_legacy
from jobcopilot.llm import ClaudeCodeClient
from jobcopilot.pipeline import run_pipeline
from jobcopilot.sources import build_sources, http_client

app = typer.Typer(help="Local job-search copilot.", no_args_is_help=True)


@app.callback()
def main(ctx: typer.Context, home: Path = typer.Option(Path.cwd(), help="Folder with config.yaml, profile.yaml and data.")):
    ctx.obj = home.expanduser().resolve()


@app.command()
def run(ctx: typer.Context):
    """Collect, filter, score and draft today's jobs."""
    home: Path = ctx.obj
    profile_path = home / "profile.yaml"
    if not profile_path.exists():
        typer.echo(f"Missing {profile_path}. Copy profile.example.yaml to profile.yaml and edit it.")
        raise typer.Exit(1)
    settings = load_settings(home / "config.yaml")
    profile = load_profile(profile_path)
    db = DB(settings.db_path)
    with http_client() as client:
        sources = build_sources(settings.sources, client)
        llm = ClaudeCodeClient(timeout_s=settings.llm.timeout_s)
        result, report = run_pipeline(settings, profile, db, sources, llm, datetime.now().astimezone())
    if result.error:
        typer.echo(f"Run failed: {result.error}")
        raise typer.Exit(1)
    typer.echo(f"{result.new} new jobs, {result.kept} passed filters, {result.drafted} drafted.")
    if result.failed_sources:
        typer.echo(f"Failed sources: {', '.join(result.failed_sources)}")
    typer.echo(f"Report: {report}")


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
