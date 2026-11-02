from datetime import datetime, timedelta, timezone

import pytest

from jobcopilot.db import DB
from jobcopilot.models import Draft, Evaluation, Job, Run

NOW = datetime(2026, 11, 2, 8, 30, tzinfo=timezone.utc)


def job(url="https://a/1", company="Acme", title="AI Engineer") -> Job:
    return Job(url=url, source="s", company=company, title=title)


@pytest.fixture
def db():
    return DB(":memory:")


def test_filter_new_drops_known_url(db):
    db.insert_jobs([job()], NOW)
    assert db.filter_new([job(), job(url="https://a/2", title="Other")]) == [job(url="https://a/2", title="Other")]


def test_filter_new_drops_same_company_title_different_url(db):
    db.insert_jobs([job(company="Acme")], NOW)
    assert db.filter_new([job(url="https://b/9", company="ACME ")]) == []


def test_filter_new_drops_in_batch_duplicates(db):
    assert len(db.filter_new([job(), job(), job(url="https://c/3")])) == 1


def test_insert_creates_new_application(db):
    db.insert_jobs([job()], NOW)
    assert db.status(job().id) == "new"


def test_set_status_applied_schedules_follow_up(db):
    db.insert_jobs([job()], NOW)
    db.set_status(job().id, "applied", NOW)
    assert db.follow_ups_due(NOW + timedelta(days=4)) == []
    due = db.follow_ups_due(NOW + timedelta(days=5))
    assert [(j.id, d) for j, d in due] == [(job().id, NOW + timedelta(days=5))]


def test_set_status_rejects_unknown_status(db):
    db.insert_jobs([job()], NOW)
    with pytest.raises(ValueError):
        db.set_status(job().id, "ghosted", NOW)


def test_save_evaluation_and_draft(db):
    db.insert_jobs([job()], NOW)
    db.save_evaluation(Evaluation(job_id=job().id, score=80, fit_reasons="r", gaps="g", model="m", prompt_version="v"))
    db.save_draft(Draft(job_id=job().id, cv_tweaks=["a", "b"], cover_note="c", outreach_message="o", contact_hint="h", model="m", prompt_version="v"))
    assert db.latest_draft(job().id).cv_tweaks == ["a", "b"]


def test_mark_llm_error(db):
    db.insert_jobs([job()], NOW)
    db.mark_llm_error(job().id)
    assert db.notes(job().id) == "llm_error"


def test_run_roundtrip(db):
    run_id = db.start_run(Run(started_at=NOW))
    db.finish_run(run_id, Run(started_at=NOW, finished_at=NOW, fetched=5, failed_sources=["hn"], error="boom"))
    assert db.get_run(run_id).failed_sources == ["hn"]
    assert db.get_run(run_id).error == "boom"


def test_known_urls(db):
    db.insert_jobs([job()], NOW)
    assert db.known_urls() == {"https://a/1"}


def test_delete_jobs_removes_job_and_application(db):
    db.insert_jobs([job()], NOW)
    db.delete_jobs([job().id])
    assert db.known_urls() == set() and db.status(job().id) is None
