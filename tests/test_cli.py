from datetime import datetime, timezone

from typer.testing import CliRunner

from jobcopilot import cli
from jobcopilot.models import Run

RUNNER = CliRunner()


def fake_pipeline(error=None):
    def run_pipeline(settings, profile, db, sources, llm, now):
        run = Run(started_at=now, finished_at=now, new=3, kept=2, scored=2, drafted=1, error=error)
        return run, (None if error else settings.reports_dir / "2026-11-02.md")
    return run_pipeline


def home_with_profile(tmp_path):
    (tmp_path / "profile.yaml").write_text("hard_filters: {}\nsummary: me\n")
    return tmp_path


def test_cli_run_prints_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline())
    result = RUNNER.invoke(cli.app, ["--home", str(home_with_profile(tmp_path)), "run"])
    assert result.exit_code == 0, result.output
    assert "2026-11-02.md" in result.output


def test_cli_run_exits_1_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline(error="Claude Code is logged out"))
    result = RUNNER.invoke(cli.app, ["--home", str(home_with_profile(tmp_path)), "run"])
    assert result.exit_code == 1
    assert "logged out" in result.output


def test_cli_run_without_profile_explains(tmp_path):
    result = RUNNER.invoke(cli.app, ["--home", str(tmp_path), "run"])
    assert result.exit_code == 1
    assert "profile.yaml" in result.output
