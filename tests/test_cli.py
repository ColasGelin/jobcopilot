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


def test_cli_daemon_starts_loop_with_schedule(monkeypatch, tmp_path):
    from datetime import time
    captured = {}

    def fake_loop(run_once, state, run_at):
        captured["run_at"] = run_at
        captured["state"] = state()

    monkeypatch.setattr(cli, "loop", fake_loop)
    result = RUNNER.invoke(cli.app, ["--home", str(home_with_profile(tmp_path)), "daemon", "--at", "07:45"])
    assert result.exit_code == 0, result.output
    assert captured == {"run_at": time(7, 45), "state": (None, None)}


def test_cli_daemon_rejects_bad_time(tmp_path):
    result = RUNNER.invoke(cli.app, ["--home", str(home_with_profile(tmp_path)), "daemon", "--at", "25:00"])
    assert result.exit_code != 0


def test_cli_serve_uses_local_defaults(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port: captured.update(host=host, port=port))
    result = RUNNER.invoke(cli.app, ["--home", str(tmp_path), "serve"])
    assert result.exit_code == 0, result.output
    assert captured == {"host": "127.0.0.1", "port": 8077}


def test_cli_serve_passes_min_score(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("min_score: 70\n")
    captured = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port: None)
    import jobcopilot.web as web
    real = web.create_app
    monkeypatch.setattr(web, "create_app", lambda db_path, **kw: captured.update(kw) or real(db_path, **kw))
    RUNNER.invoke(cli.app, ["--home", str(tmp_path), "serve"])
    assert captured.get("min_score") == 70
