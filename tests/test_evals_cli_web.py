from datetime import datetime, timezone

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from jobcopilot import cli
from jobcopilot.db import DB
from jobcopilot.evals.models import CaseResult, EvalRun, TaskResult
from jobcopilot.evals.store import EvalStore
from jobcopilot.web import create_app

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


def run_with(run_id, values, started=NOW):
    cases = [CaseResult(case_id=f"c{i}", label=f"Co{i} — AI Engineer", score=v, passed=v >= 0.5, expected="70 or more",
                        actual="85" if v else "40", note="No salary stated.") for i, v in enumerate(values)]
    mean = sum(values) / len(values)
    return EvalRun(id=run_id, started_at=started, models={"scoring": "haiku"}, prompts={"scoring": "score_v1"},
                   tasks=[TaskResult(task="scoring", metric="in_band", value=mean, ci_low=mean, ci_high=mean, n=len(values), calls=1, seconds=2.0, cases=cases)])


def client(tmp_path, runner=None):
    DB(tmp_path / "jobs.db")
    app = create_app(tmp_path / "jobs.db", clock=lambda: NOW, evals_dir=tmp_path / "evals", eval_runner=runner)
    return TestClient(app, base_url="http://localhost", headers={"HX-Request": "true"})


def test_evals_page_empty_state_says_what_to_run(tmp_path):
    body = client(tmp_path).get("/evals").text
    assert "No evaluation run yet" in body and "jobcopilot eval run" in body and 'href="/evals"' in body


def test_evals_page_shows_verdict_rows_and_failures(tmp_path):
    store = EvalStore(tmp_path / "evals")
    store.save(run_with("base", [1] * 20))
    store.promote("base")
    store.save(run_with("new", [0] * 12 + [1] * 8, started=NOW.replace(hour=10)))
    body = client(tmp_path).get("/evals").text
    for text in ("Blocked", "Match scoring", "40%", "−60", "Co3 — AI Engineer", "70 or more", "No salary stated.", "Got worse"):
        assert text in body, text


def test_promote_sets_the_baseline(tmp_path):
    store = EvalStore(tmp_path / "evals")
    store.save(run_with("a", [1, 1, 0]))
    c = client(tmp_path)
    assert "No baseline yet" in c.get("/evals").text
    r = c.post("/evals/promote/a")
    assert r.status_code == 200 and store.baseline().id == "a" and "This run is the baseline" in c.get("/evals").text
    assert c.post("/evals/promote/missing").status_code == 404
    assert TestClient(c.app, base_url="http://localhost").post("/evals/promote/a").status_code == 403


def test_run_button_runs_in_the_background_and_saves(tmp_path):
    asked = []

    def runner(tasks):
        asked.append(tasks)
        return run_with("fresh", [1, 1])

    c = client(tmp_path, runner)
    r = c.post("/evals/run", data={"tasks": "scoring"})
    assert r.status_code == 200
    c.app.state.eval_thread.join(timeout=5)
    assert asked == [["scoring"]] and EvalStore(tmp_path / "evals").latest().id == "fresh"


def test_run_button_reports_a_crash(tmp_path):
    def runner(tasks):
        raise RuntimeError("Claude Code is logged out")

    c = client(tmp_path, runner)
    c.post("/evals/run", data={"tasks": "scoring"})
    c.app.state.eval_thread.join(timeout=5)
    assert "Claude Code is logged out" in c.get("/evals").text


def test_run_is_not_offered_when_not_configured(tmp_path):
    c = client(tmp_path)
    assert c.post("/evals/run", data={"tasks": "scoring"}).status_code == 409


def test_cli_gate_exits_1_when_blocked_and_promote_sets_baseline(tmp_path, monkeypatch):
    store = EvalStore(tmp_path / "evals")
    store.save(run_with("base", [1] * 20))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--home", str(tmp_path), "eval", "promote", "base"])
    assert result.exit_code == 0 and store.baseline().id == "base"
    monkeypatch.setattr(cli, "_eval_run", lambda home, tasks, limit: run_with("new", [0] * 12 + [1] * 8))
    result = runner.invoke(cli.app, ["--home", str(tmp_path), "eval", "run", "--gate"])
    assert result.exit_code == 1 and "BLOCKED" in result.output and "Match scoring" in result.output
    assert store.latest().id == "new"
    monkeypatch.setattr(cli, "_eval_run", lambda home, tasks, limit: run_with("ok", [1] * 20))
    result = runner.invoke(cli.app, ["--home", str(tmp_path), "eval", "run", "--gate"])
    assert result.exit_code == 0 and "SAFE" in result.output


def test_cli_status_shows_the_latest_run(tmp_path):
    EvalStore(tmp_path / "evals").save(run_with("a", [1, 0]))
    result = CliRunner().invoke(cli.app, ["--home", str(tmp_path), "eval", "status"])
    assert result.exit_code == 0 and "NO BASELINE" in result.output and "50%" in result.output
