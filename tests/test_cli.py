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


def test_cli_cv_writes_main_pdf(tmp_path):
    import shutil
    from pathlib import Path

    shutil.copy(Path(__file__).parent / "fixtures" / "cv.example.yaml", tmp_path / "cv.yaml")
    result = RUNNER.invoke(cli.app, ["--home", str(tmp_path), "cv"])
    assert result.exit_code == 0, result.output
    pdf = tmp_path / "cvs" / "cv-main.pdf"
    assert pdf.read_bytes()[:4] == b"%PDF" and "cv-main.pdf" in result.output


def test_cli_cv_missing_bank(tmp_path):
    result = RUNNER.invoke(cli.app, ["--home", str(tmp_path), "cv"])
    assert result.exit_code == 1 and "cv.yaml" in result.output


def test_cli_cv_job_unknown(tmp_path):
    import shutil
    from pathlib import Path

    shutil.copy(Path(__file__).parent / "fixtures" / "cv.example.yaml", tmp_path / "cv.yaml")
    result = RUNNER.invoke(cli.app, ["--home", str(tmp_path), "cv", "--job", "nope"])
    assert result.exit_code == 1 and "nope" in result.output


def test_cli_cv_job_tailors(monkeypatch, tmp_path):
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path

    from conftest import make_job
    from jobcopilot.db import DB
    from jobcopilot.llm import FakeLLMClient

    shutil.copy(Path(__file__).parent / "fixtures" / "cv.example.yaml", tmp_path / "cv.yaml")
    job = make_job(1, company="Acme")
    DB(tmp_path / "jobs.db").insert_jobs([job], datetime(2026, 11, 3, tzinfo=timezone.utc))
    fake = FakeLLMClient([{
        "language": "en", "headline": "AI Engineer", "summary": "AI engineer.",
        "experience": [{"id": "acme", "bullets": [{"source_id": "acme.rag", "text": "Built a RAG assistant."}]},
                       {"id": "beta", "bullets": [{"source_id": "beta.api", "text": "Built FastAPI services."}]}],
        "projects": [], "skills": [], "extras": [],
    }])
    monkeypatch.setattr(cli, "ClaudeCodeClient", lambda **kw: fake)
    result = RUNNER.invoke(cli.app, ["--home", str(tmp_path), "cv", "--job", job.id])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cvs" / f"{job.id}-acme.pdf").exists()
    assert DB(tmp_path / "jobs.db").latest_tailored_cv(job.id) is not None


def test_cli_run_prepares_applications_when_enabled(monkeypatch, tmp_path):
    from jobcopilot.apply.prepare import Preparer

    calls = []
    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline())
    monkeypatch.setattr(Preparer, "prepare_pending", lambda self, now: calls.append(now) or 2)
    result = RUNNER.invoke(cli.app, ["--home", str(home_with_profile(tmp_path)), "run"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and "2 applications prepared" in result.output


def test_cli_run_skips_preparing_when_disabled_or_failed(monkeypatch, tmp_path):
    from jobcopilot.apply.prepare import Preparer

    calls = []
    monkeypatch.setattr(Preparer, "prepare_pending", lambda self, now: calls.append(now) or 0)
    home = home_with_profile(tmp_path)
    (home / "config.yaml").write_text("prepare: {enabled: false}\n")
    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline())
    RUNNER.invoke(cli.app, ["--home", str(home), "run"])
    (home / "config.yaml").write_text("")
    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline(error="boom"))
    RUNNER.invoke(cli.app, ["--home", str(home), "run"])
    assert calls == []


def test_cli_run_survives_preparing_crash(monkeypatch, tmp_path):
    from jobcopilot.apply.prepare import Preparer

    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline())
    monkeypatch.setattr(Preparer, "prepare_pending", lambda self, now: (_ for _ in ()).throw(RuntimeError("x")))
    result = RUNNER.invoke(cli.app, ["--home", str(home_with_profile(tmp_path)), "run"])
    assert result.exit_code == 0 and "Preparing applications failed" in result.output


def test_cli_serve_passes_preparer_factory_only_with_profile(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port: None)
    import jobcopilot.web as web
    real = web.create_app
    monkeypatch.setattr(web, "create_app", lambda db_path, **kw: captured.update(kw) or real(db_path, **kw))
    RUNNER.invoke(cli.app, ["--home", str(tmp_path), "serve"])
    assert captured["preparer_factory"] is None
    RUNNER.invoke(cli.app, ["--home", str(home_with_profile(tmp_path)), "serve"])
    preparer = captured["preparer_factory"]()
    assert preparer.profile.summary == "me" and preparer.settings.data_dir == tmp_path


def test_cli_agent_install_uninstall_and_run(monkeypatch, tmp_path):
    from jobcopilot.agent import launchd, loop

    calls = []
    monkeypatch.setattr(launchd, "install", lambda uv, repo, dashboard: calls.append(("install", dashboard)) or tmp_path / "x.plist")
    monkeypatch.setattr(launchd, "uninstall", lambda: calls.append(("uninstall",)))
    monkeypatch.setattr(loop, "run_agent", lambda dashboard: calls.append(("run", dashboard)))
    assert RUNNER.invoke(cli.app, ["agent", "--install"]).exit_code == 0
    assert RUNNER.invoke(cli.app, ["agent", "--uninstall"]).exit_code == 0
    assert RUNNER.invoke(cli.app, ["agent", "--dashboard", "http://localhost:9000"]).exit_code == 0
    assert calls == [("install", "http://localhost:8077"), ("uninstall",), ("run", "http://localhost:9000")]
