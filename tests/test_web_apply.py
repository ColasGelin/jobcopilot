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

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"
FORM = FormSnapshot(url="https://jobs/1", fields=[
    FormField(key="email", selector="#email", label="Email", kind="email"),
    FormField(key="why", selector="#why", label="Why Acme?", kind="textarea"),
    FormField(key="cv", selector="#cv", label="Resume/CV", kind="file"),
    FormField(key="gender", selector="#gender", label="Gender", kind="select", options=["F", "M"]),
])
ANSWERS = [
    Answer(field_key="email", label="Email", kind="fact", profile_key="email", draft="alex@example.com"),
    Answer(field_key="why", label="Why Acme?", kind="open", draft="I built a RAG assistant.", problems=["Why Acme?: AWS not in the source"]),
    Answer(field_key="cv", label="Resume/CV", kind="cv", draft="CV"),
    Answer(field_key="gender", label="Gender", kind="leave"),
]


def seed_app(db: DB, job_id: str, status="ready", quote=None) -> int:
    app = db.create_prepared(job_id, status, NOW)
    db.update_prepared(app, NOW, form=FORM, ai_ban_quote=quote)
    db.save_answers(app, ANSWERS, "m", "apply_v1", NOW)
    return app


class FakePreparer:
    """Records calls; opens its DB per call like the real per-request Preparer."""

    def __init__(self, path):
        self.path, self.calls = path, []

    def prepare_job(self, job_id, now):
        self.calls.append(job_id)
        db = DB(self.path)
        return db.get_prepared(seed_app(db, job_id))


@pytest.fixture
def home(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    job = make_job(1, company="Acme")
    db = DB(tmp_path / "jobs.db")
    db.insert_jobs([job], NOW)
    db.save_evaluation(Evaluation(job_id=job.id, score=85, fit_reasons="fit", gaps="gap", model="m", prompt_version="v"))
    return tmp_path, job, db


def client(tmp, preparer=True, htmx=True):
    fake = FakePreparer(tmp / "jobs.db") if preparer else None
    app = create_app(tmp / "jobs.db", clock=lambda: NOW, cv_path=tmp / "cv.yaml", cvs_dir=tmp / "cvs",
                     preparer_factory=(lambda: fake) if fake else None)
    headers = {"HX-Request": "true"} if htmx else {}
    return TestClient(app, base_url="http://localhost", headers=headers), fake


def test_card_offers_prepare_now_then_shows_badge(home):
    tmp, job, db = home
    c, fake = client(tmp)
    assert f'hx-post="/jobs/{job.id}/prepare"' in c.get("/").text
    r = c.post(f"/jobs/{job.id}/prepare")
    assert r.status_code == 200 and "Application ready" in r.text and fake.calls == [job.id]
    assert f'href="/jobs/{job.id}/application"' in c.get("/").text


def test_prepare_now_without_preparer(home):
    tmp, job, _ = home
    c, _ = client(tmp, preparer=False)
    assert "Preparing is not configured" in c.post(f"/jobs/{job.id}/prepare").text


def test_prepare_errors_are_shown(home, monkeypatch):
    tmp, job, _ = home
    c, fake = client(tmp)
    monkeypatch.setattr(fake, "prepare_job", lambda job_id, now: (_ for _ in ()).throw(RuntimeError("browser crashed")))
    r = c.post(f"/jobs/{job.id}/prepare")
    assert r.status_code == 200 and "browser crashed" in r.text


def test_application_page_shows_answers_problems_and_ai_ban(home):
    tmp, job, db = home
    seed_app(db, job.id, quote="Please do not use AI to write your answers.")
    c, _ = client(tmp)
    page = c.get(f"/jobs/{job.id}/application")
    assert page.status_code == 200
    for text in ("Please do not use AI to write your answers.", "alex@example.com", "I built a RAG assistant.",
                 "AWS not in the source", "Tailored CV", "Left for you", "Fill form", "Mark applied"):
        assert text in page.text
    assert c.get("/jobs/nope/application").status_code == 404


def test_needs_you_page_offers_open_on_mac(home):
    tmp, job, db = home
    app = db.create_prepared(job.id, "needs_you", NOW, reason="Sign-in or account creation required")
    c, _ = client(tmp)
    page = c.get(f"/jobs/{job.id}/application").text
    assert "Sign-in or account creation required" in page and f'hx-post="/applications/{app}/open"' in page


def test_autosave_final(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    aid = db.answers(app)[1].id
    c, _ = client(tmp)
    assert c.post(f"/answers/{aid}", data={"final": "My own words."}).status_code == 204
    assert db.answers(app)[1].final == "My own words."
    assert c.post("/answers/999", data={"final": "x"}).status_code == 404


def test_fill_saves_pending_edits_then_queues(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    aid = db.answers(app)[1].id
    c, _ = client(tmp)
    r = c.post(f"/applications/{app}/fill", data={f"final-{aid}": "Edited"})
    assert r.status_code == 200 and "Waiting for your Mac" in r.text
    assert db.answers(app)[1].final == "Edited" and db.latest_command(app).action == "fill"


def test_open_continue_and_applied_flow(home):
    tmp, job, db = home
    app = db.create_prepared(job.id, "needs_you", NOW, reason="Sign-in or account creation required")
    c, _ = client(tmp)
    c.post(f"/applications/{app}/open")
    cmd = db.latest_command(app)
    assert cmd.action == "read" and cmd.status == "queued"
    db.set_command(cmd.id, "waiting", NOW)
    assert "Continue" in c.get(f"/jobs/{job.id}/application").text
    c.post(f"/applications/{app}/continue")
    assert db.get_command(cmd.id).status == "queued"
    r = c.post(f"/applications/{app}/applied")
    assert "Applied" in r.text and db.get_prepared(app).status == "applied" and db.status(job.id) == "applied"


def test_unfilled_fields_listed_after_fill(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    cmd = db.queue_command(app, "fill", NOW)
    db.set_command(cmd, "done", NOW, result={"filled": ["email"], "unfilled": [{"key": "why", "label": "Why Acme?", "why": "not found"}]})
    c, _ = client(tmp)
    page = c.get(f"/jobs/{job.id}/application").text
    assert "Why Acme?" in page and "not found" in page


def test_routes_require_htmx_and_404_unknown(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    plain, _ = client(tmp, htmx=False)
    for path in (f"/jobs/{job.id}/prepare", f"/applications/{app}/fill", f"/applications/{app}/open",
                 f"/applications/{app}/continue", f"/applications/{app}/applied", f"/answers/1"):
        assert plain.post(path).status_code == 403
    c, _ = client(tmp)
    assert c.post("/jobs/nope/prepare").status_code == 404
    for action in ("fill", "open", "continue", "applied"):
        assert c.post(f"/applications/999/{action}").status_code == 404


def test_read_next_step_offered_after_fill_and_try_again_when_stuck(home):
    tmp, job, db = home
    app = seed_app(db, job.id, status="filled")
    c, _ = client(tmp)
    page = c.get(f"/jobs/{job.id}/application").text
    assert "Read next step" in page and f'hx-post="/applications/{app}/open"' in page
    db.update_prepared(app, NOW, status="reading")
    assert "Try again" in c.get(f"/jobs/{job.id}/application").text
