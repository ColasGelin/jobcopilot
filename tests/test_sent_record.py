"""The record of what was sent with each application: saved when applying, never changed by later drafts."""

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.cv.bank import load_bank
from jobcopilot.cv.content import main_content
from jobcopilot.db import DB
from jobcopilot.models import Evaluation
from jobcopilot.web import create_app
from test_web_apply import ANSWERS, seed_app

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"


def add(db, job, score=85):
    db.insert_jobs([job], NOW)
    db.save_evaluation(Evaluation(job_id=job.id, score=score, fit_reasons="fit", gaps="gap", model="m", prompt_version="v"))
    return job


@pytest.fixture
def home(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    db = DB(tmp_path / "jobs.db")
    return tmp_path, add(db, make_job(1, company="Acme")), db


def client(tmp):
    app = create_app(tmp / "jobs.db", clock=lambda: NOW, cv_path=tmp / "cv.yaml", cvs_dir=tmp / "cvs")
    return TestClient(app, base_url="http://localhost", headers={"HX-Request": "true"})


def pairs(record):
    return [(a["question"], a["answer"], a["source"]) for a in record["answers"]]


def test_autosave_reads_the_field_the_way_the_page_sends_it(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    aid = db.answers(app)[1].id
    assert client(tmp).post(f"/answers/{aid}", data={f"final-{aid}": "My own words."}).status_code == 204
    assert db.answers(app)[1].final == "My own words."


def test_applying_saves_answers_cover_letter_and_cv(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    db.set_final(db.answers(app)[1].id, "My own words.", NOW)
    db.save_cover_letter(job.id, "Dear Acme,", [], "m", "v", NOW)
    db.save_tailored_cv(job.id, main_content(load_bank(tmp / "cv.yaml")), 1, "m", "v")
    assert db.sent_record(job.id) is None
    db.set_status(job.id, "applied", NOW)
    record = db.sent_record(job.id)
    assert pairs(record) == [("Email", "alex@example.com", "form"), ("Why Acme?", "My own words.", "form")]
    assert record["cover"] == "Dear Acme," and record["cv"] is not None and record["saved_at"] == NOW


def test_record_stays_when_drafts_are_redone_or_the_job_is_applied_again(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    db.save_cover_letter(job.id, "Dear Acme,", [], "m", "v", NOW)
    db.set_status(job.id, "applied", NOW)
    db.delete_answers(app)
    db.save_cover_letter(job.id, "Another letter", [], "m", "v", NOW)
    db.set_status(job.id, "applied", NOW + timedelta(days=1))
    record = db.sent_record(job.id)
    assert len(record["answers"]) == 2 and record["cover"] == "Dear Acme," and record["saved_at"] == NOW


def test_blank_answer_keeps_the_draft_and_says_so(home):
    tmp, job, db = home
    app = seed_app(db, job.id)
    db.set_final(db.answers(app)[1].id, "", NOW)
    db.set_status(job.id, "applied", NOW)
    assert ("Why Acme?", ANSWERS[1].draft, "draft") in pairs(db.sent_record(job.id))


def test_record_uses_the_same_role_found_on_another_board(home):
    tmp, job, db = home
    twin = add(db, make_job(2, company="Acme", title=job.title))
    db.save_cover_letter(twin.id, "Dear Acme,", [], "m", "v", NOW)
    db.set_status(job.id, "applied", NOW)
    assert db.sent_record(job.id)["cover"] == "Dear Acme,"


def test_applications_made_before_this_existed_get_a_record(home):
    tmp, job, db = home
    seed_app(db, job.id)
    db.set_status(job.id, "applied", NOW)
    db.conn.execute("DELETE FROM sent_answers")
    db.conn.execute("DELETE FROM sent_records")
    db.conn.commit()
    record = DB(tmp / "jobs.db").sent_record(job.id)
    assert record["saved_at"] == NOW and len(record["answers"]) == 2


def test_record_page_shows_and_edits_what_was_sent(home):
    tmp, job, db = home
    seed_app(db, job.id)
    db.save_tailored_cv(job.id, main_content(load_bank(tmp / "cv.yaml")), 1, "m", "v")
    db.set_status(job.id, "applied", NOW)
    c = client(tmp)
    page = c.get(f"/jobs/{job.id}/sent")
    assert page.status_code == 200
    for text in ("Acme", "What I sent", "alex@example.com", "I built a RAG assistant.", f"/jobs/{job.id}/sent/cv.pdf"):
        assert text in page.text
    assert c.get(f"/jobs/{job.id}/sent/cv.pdf").headers["content-type"] == "application/pdf"

    r = c.post(f"/jobs/{job.id}/sent/answers", data={"question": "Salary expectations?", "answer": "32k"})
    assert r.status_code == 200 and "Salary expectations?" in r.text
    added = db.sent_record(job.id)["answers"][-1]
    assert (added["question"], added["answer"], added["source"]) == ("Salary expectations?", "32k", "manual")
    assert c.post(f"/sent/answers/{added['id']}", data={"answer": "33k"}).status_code == 204
    assert c.post(f"/jobs/{job.id}/sent/cover", data={"text": "The letter I pasted"}).status_code == 204
    assert c.post(f"/jobs/{job.id}/sent/notes", data={"text": "Spoke to Ana"}).status_code == 204
    record = db.sent_record(job.id)
    assert record["answers"][-1]["answer"] == "33k" and record["cover"] == "The letter I pasted" and record["notes"] == "Spoke to Ana"
    assert c.post(f"/sent/answers/{added['id']}/delete").status_code == 200
    assert len(db.sent_record(job.id)["answers"]) == 2
    assert c.post(f"/jobs/{job.id}/sent/answers", data={"question": " ", "answer": "x"}).status_code == 400
    assert c.post("/sent/answers/999", data={"answer": "x"}).status_code == 404


def test_record_page_needs_an_application(home):
    tmp, job, db = home
    c = client(tmp)
    assert c.get(f"/jobs/{job.id}/sent").status_code == 404 and c.get("/jobs/nope/sent").status_code == 404
    assert c.get(f"/jobs/{job.id}/sent/cv.pdf").status_code == 404


def test_applied_list_links_every_application_to_its_record(home):
    tmp, job, db = home
    db.set_status(job.id, "applied", NOW)
    assert f'href="/jobs/{job.id}/sent"' in client(tmp).get("/applied").text


def test_i_applied_saves_the_record_and_opens_it(home):
    tmp, job, db = home
    app = seed_app(db, job.id, status="filled")
    c = client(tmp)
    page = c.get(f"/jobs/{job.id}/application").text
    assert f'hx-post="/jobs/{job.id}/applied"' in page and "What I sent" not in page
    r = c.post(f"/jobs/{job.id}/applied")
    assert r.headers["HX-Redirect"] == f"/jobs/{job.id}/sent"
    assert db.status(job.id) == "applied" and db.get_prepared(app).status == "applied" and db.sent_record(job.id)
    page = c.get(f"/jobs/{job.id}/application").text
    assert "What I sent" in page and f'hx-post="/jobs/{job.id}/applied"' not in page and "Fill the form" not in page


def test_job_page_opens_before_anything_is_prepared(home):
    tmp, job, db = home
    c = client(tmp)
    page = c.get(f"/jobs/{job.id}/application")
    assert page.status_code == 200 and "Prepare application" in page.text and "Tailor CV for this job" in page.text
    r = c.post(f"/jobs/{job.id}/status?page=1", data={"status": "shortlisted"})
    assert r.headers["HX-Refresh"] == "true" and db.status(job.id) == "shortlisted"
    assert c.post(f"/jobs/{job.id}/applied").headers["HX-Redirect"]  # applied by hand, nothing prepared
    assert db.sent_record(job.id)["answers"] == []
