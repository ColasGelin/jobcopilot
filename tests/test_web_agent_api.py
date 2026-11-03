import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.apply.models import Answer, FormField, FormSnapshot
from jobcopilot.db import DB
from jobcopilot.models import Evaluation
from jobcopilot.web import create_app
from test_web_cv import GOOD
from jobcopilot.llm import FakeLLMClient

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"
AGENT = {"X-Jobcopilot-Agent": "1"}
FORM = FormSnapshot(url="https://jobs/1", fields=[
    FormField(key="email", selector="#email", label="Email", kind="email"),
    FormField(key="why", selector="#why", label="Why?", kind="textarea"),
    FormField(key="gender", selector="#g", label="Gender", kind="select", options=["F", "M"]),
    FormField(key="cv", selector="#cv", label="Resume", kind="file"),
    FormField(key="more", selector="#more", label="More?", kind="textarea", step=1),
])


class FakePreparer:
    def __init__(self):
        self.ingested = []

    def ingest(self, app_id, form, now):
        self.ingested.append((app_id, form))
        return type("App", (), {"status": "ready"})()


@pytest.fixture
def env(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    job = make_job(1, company="Acme")
    db = DB(tmp_path / "jobs.db")
    db.insert_jobs([job], NOW)
    db.save_evaluation(Evaluation(job_id=job.id, score=85, fit_reasons="f", gaps="g", model="m", prompt_version="v"))
    app = db.create_prepared(job.id, "ready", NOW)
    db.update_prepared(app, NOW, form=FORM)
    db.save_answers(app, [
        Answer(field_key="email", label="Email", kind="fact", draft="alex@example.com"),
        Answer(field_key="why", label="Why?", kind="open", draft="Because."),
        Answer(field_key="gender", label="Gender", kind="leave"),
        Answer(field_key="cv", label="Resume", kind="cv", draft="CV"),
    ], "m", "v", NOW)
    fake = FakePreparer()
    llm = FakeLLMClient([GOOD])
    web = create_app(tmp_path / "jobs.db", clock=lambda: NOW, cv_path=tmp_path / "cv.yaml", cvs_dir=tmp_path / "cvs",
                     llm_factory=lambda: llm, preparer_factory=lambda: fake)
    return TestClient(web, base_url="http://localhost"), db, app, job, fake


def test_api_requires_agent_header(env):
    c, _, app, *_ = env
    for method, path in (("get", "/api/agent/next"), ("get", "/api/agent/commands/1"), ("post", "/api/agent/commands/1/result"),
                         ("get", f"/api/applications/{app}"), ("get", f"/api/applications/{app}/cv.pdf"),
                         ("post", f"/api/applications/{app}/form")):
        assert getattr(c, method)(path).status_code == 403


def test_next_and_result_cycle(env):
    c, db, app, job, _ = env
    assert c.get("/api/agent/next", headers=AGENT).status_code == 204
    cmd = db.queue_command(app, "read", NOW)
    r = c.get("/api/agent/next", headers=AGENT)
    assert r.json() == {"id": cmd, "app_id": app, "action": "read", "url": job.url}
    assert c.get(f"/api/agent/commands/{cmd}", headers=AGENT).json() == {"id": cmd, "status": "taken"}
    assert c.post(f"/api/agent/commands/{cmd}/result", headers=AGENT, json={"status": "waiting"}).status_code == 200
    assert db.get_command(cmd).status == "waiting"
    assert c.post(f"/api/agent/commands/{cmd}/result", headers=AGENT, json={"status": "bogus"}).status_code == 400


def test_fill_done_marks_filled(env):
    c, db, app, *_ = env
    cmd = db.queue_command(app, "fill", NOW)
    body = {"status": "done", "filled": ["email"], "unfilled": [{"key": "why", "label": "Why?", "why": "not found"}]}
    c.post(f"/api/agent/commands/{cmd}/result", headers=AGENT, json=body)
    assert db.get_prepared(app).status == "filled" and db.get_command(cmd).result["unfilled"][0]["why"] == "not found"


def test_application_payload_latest_step_only(env):
    c, db, app, job, _ = env
    data = c.get(f"/api/applications/{app}", headers=AGENT).json()
    assert data["job_url"] == job.url
    assert {f["key"]: (f["kind"], f["answer_kind"], f["value"]) for f in data["fields"]} == {
        "email": ("email", "fact", "alex@example.com"), "why": ("textarea", "open", "Because."), "cv": ("file", "cv", "CV")}
    assert next(f for f in data["fields"] if f["key"] == "email")["selector"] == "#email"
    db.save_answers(app, [Answer(field_key="more", label="More?", kind="open", draft="Yes.", step=1)], "m", "v", NOW)
    data = c.get(f"/api/applications/{app}", headers=AGENT).json()
    assert [f["key"] for f in data["fields"]] == ["more"]


def test_edited_final_is_what_gets_filled(env):
    c, db, app, *_ = env
    db.set_final(db.answers(app)[1].id, "My words.", NOW)
    data = c.get(f"/api/applications/{app}", headers=AGENT).json()
    assert next(f for f in data["fields"] if f["key"] == "why")["value"] == "My words."


def test_cv_download_name(env):
    c, db, app, job, _ = env
    assert c.get(f"/api/applications/{app}/cv.pdf", headers=AGENT).status_code == 404  # no tailored CV yet
    c.post(f"/jobs/{job.id}/cv", headers={"HX-Request": "true"})
    r = c.get(f"/api/applications/{app}/cv.pdf", headers=AGENT)
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and "Alex-Martin-CV.pdf" in r.headers["content-disposition"]


def test_form_ingest_calls_preparer(env):
    c, _, app, _, fake = env
    r = c.post(f"/api/applications/{app}/form", headers=AGENT, json=FORM.model_dump())
    assert r.json() == {"status": "ready"} and fake.ingested[0][0] == app and fake.ingested[0][1] == FORM
    assert c.post(f"/api/applications/{app}/form", headers=AGENT, json={"nope": 1}).status_code == 422


def test_unknown_ids_404(env):
    c, *_ = env
    assert c.get("/api/agent/commands/999", headers=AGENT).status_code == 404
    assert c.post("/api/agent/commands/999/result", headers=AGENT, json={"status": "done"}).status_code == 404
    assert c.get("/api/applications/999", headers=AGENT).status_code == 404
    assert c.get("/api/applications/999/cv.pdf", headers=AGENT).status_code == 404
    assert c.post("/api/applications/999/form", headers=AGENT, json=FORM.model_dump()).status_code == 404
