"""LinkedIn outreach reminders: one per application sent, until it is marked sent or skipped."""

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.db import DB
from jobcopilot.drafting import DRAFT_PROMPT_VERSION
from jobcopilot.models import Draft, Evaluation
from jobcopilot.web import create_app

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
HX = {"HX-Request": "true"}
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"


def draft(job_id, version=DRAFT_PROMPT_VERSION, search="Acme talent acquisition"):
    return Draft(job_id=job_id, cv_tweaks=["t"], cover_note="c", outreach_message="Hi Ana, I applied for the AI role. Colas",
                 contact_hint="Ana Ruiz, recruiter for the AI team", contact_search=search, model="m", prompt_version=version)


@pytest.fixture
def db(tmp_path):
    store = DB(tmp_path / "jobs.db")
    jobs = [make_job(i, company=f"Co{i}") for i in (1, 2, 3)]
    store.insert_jobs(jobs, NOW)
    for j in jobs:
        store.save_evaluation(Evaluation(job_id=j.id, score=80, fit_reasons="f", gaps="g", model="m", prompt_version="v"))
    return store, jobs


def test_due_for_applications_sent_until_marked(db):
    store, jobs = db
    store.set_status(jobs[0].id, "applied", NOW)
    store.set_status(jobs[1].id, "applied", NOW.replace(hour=10))
    store.set_status(jobs[2].id, "interview", NOW)  # past the point of a first message
    store.save_draft(draft(jobs[0].id))
    due = store.outreach_due()
    assert [j.id for j, _ in due] == [jobs[1].id, jobs[0].id]  # latest application first
    assert dict((j.id, d) for j, d in due)[jobs[0].id].contact_search == "Acme talent acquisition"
    store.set_outreach(jobs[0].id, "sent", NOW)
    store.set_outreach(jobs[1].id, "skipped", NOW)
    assert store.outreach_due() == []
    with pytest.raises(ValueError):
        store.set_outreach(jobs[0].id, "maybe", NOW)


def test_deleting_a_job_deletes_its_outreach(db):
    store, jobs = db
    store.set_outreach(jobs[0].id, "sent", NOW)
    store.delete_jobs([jobs[0].id])
    assert store.conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0] == 0


def test_contact_search_column_added_to_existing_db(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, job_id TEXT, cv_tweaks TEXT, cover_note TEXT,"
                " outreach_message TEXT, contact_hint TEXT, model TEXT, prompt_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    old.execute("INSERT INTO drafts (job_id, cv_tweaks, cover_note, outreach_message, contact_hint, model, prompt_version)"
                " VALUES ('j', '[]', 'c', 'o', 'h', 'm', 'draft_v1')")
    old.commit(); old.close()
    store = DB(path)
    assert store.latest_draft("j").contact_search == ""  # old drafts load
    store.save_draft(draft("j"))
    assert store.latest_draft("j").contact_search == "Acme talent acquisition"


def test_preparer_writes_the_outreach_draft(db, tmp_path):
    from jobcopilot.apply.prepare import Preparer
    from jobcopilot.config import HardFilters, Profile, Settings
    from jobcopilot.llm import FakeLLMClient

    store, jobs = db
    llm = FakeLLMClient([{"cv_tweaks": ["a", "b"], "cover_note": "c", "outreach_message": "Hola Ana", "contact_hint": "Ana",
                          "contact_search": "Ana Ruiz Co1"}])
    p = Preparer(db=store, settings=Settings(data_dir=tmp_path), profile=Profile(hard_filters=HardFilters(), summary="s"),
                 llm=llm, read=lambda job: None, bank_loader=lambda: None)
    p.write_outreach(jobs[0].id)
    d = store.latest_draft(jobs[0].id)
    assert (d.outreach_message, d.contact_search, d.prompt_version) == ("Hola Ana", "Ana Ruiz Co1", DRAFT_PROMPT_VERSION)
    assert "Co1" in llm.calls[0][0]


class FakePreparer:
    def __init__(self, path):
        self.path, self.written = path, []

    def write_outreach(self, job_id):
        self.written.append(job_id)
        DB(self.path).save_draft(draft(job_id, search="Ana Ruiz Co1"))


@pytest.fixture
def web(db, tmp_path):
    store, jobs = db
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    fake = FakePreparer(tmp_path / "jobs.db")
    app = create_app(tmp_path / "jobs.db", clock=lambda: NOW, cv_path=tmp_path / "cv.yaml", preparer_factory=lambda: fake)
    return TestClient(app, base_url="http://localhost"), store, jobs, fake


def test_dashboard_reminds_who_to_message_and_what_to_send(web):
    c, store, jobs, _ = web
    assert "Message someone on LinkedIn" not in c.get("/").text  # nothing applied yet
    store.set_status(jobs[0].id, "applied", NOW)
    store.save_draft(draft(jobs[0].id))
    page = c.get("/").text
    assert "Message someone on LinkedIn" in page and "Ana Ruiz, recruiter for the AI team" in page
    assert "Hi Ana, I applied for the AI role. Colas" in page and f'data-copy="outreach-msg-{jobs[0].id}"' in page
    link = page.split('href="https://www.linkedin.com/search/results/people/?')[1].split('"')[0]
    assert parse_qs(urlsplit("?" + link.replace("&amp;", "&")).query)["keywords"] == ["Acme talent acquisition"]


def test_old_or_missing_draft_offers_to_write_the_message(web):
    c, store, jobs, fake = web
    store.set_status(jobs[0].id, "applied", NOW)
    store.save_draft(draft(jobs[0].id, version="draft_v1"))  # written before applying, not a LinkedIn note
    page = c.get("/").text
    assert "Write the message" in page and "linkedin.com/search" not in page
    r = c.post(f"/jobs/{jobs[0].id}/outreach", headers=HX)
    assert fake.written == [jobs[0].id] and "keywords=Ana+Ruiz+Co1" in r.text and "Write it again" in r.text


def test_write_failure_shown_on_the_row(web, monkeypatch):
    c, store, jobs, fake = web
    monkeypatch.setattr(fake, "write_outreach", lambda job_id: (_ for _ in ()).throw(RuntimeError("Claude timed out")))
    r = c.post(f"/jobs/{jobs[0].id}/outreach", headers=HX)
    assert "Writing the message failed: Claude timed out" in r.text


def test_sent_and_skip_clear_the_reminder(web):
    c, store, jobs, _ = web
    for j in jobs[:2]:
        store.set_status(j.id, "applied", NOW)
    r = c.post(f"/jobs/{jobs[0].id}/outreach/done", data={"outcome": "sent"}, headers=HX)
    assert r.status_code == 200 and r.text == ""
    assert c.post(f"/jobs/{jobs[1].id}/outreach/done", data={"outcome": "skipped"}, headers=HX).status_code == 200
    assert "Message someone on LinkedIn" not in c.get("/").text
    assert c.post(f"/jobs/{jobs[0].id}/outreach/done", data={"outcome": "maybe"}, headers=HX).status_code == 400
    assert c.post("/jobs/nope/outreach/done", data={"outcome": "sent"}, headers=HX).status_code == 404
    assert c.post(f"/jobs/{jobs[0].id}/outreach/done", data={"outcome": "sent"}).status_code == 403
    assert c.post(f"/jobs/{jobs[0].id}/outreach").status_code == 403
