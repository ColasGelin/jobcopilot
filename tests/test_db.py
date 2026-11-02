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


def test_last_success_day_and_last_attempt(db):
    assert db.last_success_day() is None and db.last_attempt_at() is None
    ok = db.start_run(Run(started_at=NOW))
    db.finish_run(ok, Run(started_at=NOW, finished_at=NOW))
    later = NOW + timedelta(days=1)
    bad = db.start_run(Run(started_at=later))
    db.finish_run(bad, Run(started_at=later, finished_at=later, error="logged out"))
    assert db.last_success_day() == NOW.date()
    assert db.last_attempt_at() == later


# --- dashboard queries -------------------------------------------------------

def _eval(j, score):
    return Evaluation(job_id=j.id, score=score, fit_reasons="fit", gaps="gap", model="m", prompt_version="v")


def _draft(j, note):
    return Draft(job_id=j.id, cv_tweaks=["t"], cover_note=note, outreach_message="o", contact_hint="h", model="m", prompt_version="v")


def test_matches_for_day_sorted_with_latest_draft(db):
    a, b = job("https://a/1", "A", "T1"), job("https://b/2", "B", "T2")
    db.insert_jobs([a, b], NOW)
    db.save_evaluation(_eval(a, 70))
    db.save_evaluation(_eval(b, 90))
    db.save_draft(_draft(b, "old"))
    db.save_draft(_draft(b, "new"))
    matches = db.matches_for_day(NOW.date())
    assert [m.job.company for m in matches] == ["B", "A"]
    assert matches[0].draft.cover_note == "new" and matches[1].draft is None
    assert matches[0].status == "new" and matches[0].job_id == b.id


def test_matches_for_day_excludes_other_days_and_unscored(db):
    a, b, c = job("https://a/1", "A", "T1"), job("https://b/2", "B", "T2"), job("https://c/3", "C", "T3")
    db.insert_jobs([a, b], NOW)
    db.insert_jobs([c], NOW - timedelta(days=1))
    db.save_evaluation(_eval(a, 70))
    db.save_evaluation(_eval(c, 80))
    assert [m.job.company for m in db.matches_for_day(NOW.date())] == ["A"]
    assert db.latest_match_day() == NOW.date()


def test_latest_match_day_none_when_empty(db):
    assert db.latest_match_day() is None and db.match("nope") is None


def test_pipeline_groups_by_status(db):
    a, b = job("https://a/1", "A", "T1"), job("https://b/2", "B", "T2")
    db.insert_jobs([a, b], NOW)
    db.save_evaluation(_eval(a, 70))
    db.set_status(a.id, "applied", NOW)
    db.set_status(b.id, "interview", NOW)
    p = db.pipeline()
    assert list(p) == ["shortlisted", "applied", "interview", "offer", "rejected"]
    assert [m.job.company for m in p["applied"]] == ["A"]
    assert p["interview"][0].evaluation.score == 0  # no evaluation → placeholder


def test_follow_up_due_ids(db):
    db.insert_jobs([job()], NOW)
    db.set_status(job().id, "applied", NOW)
    assert db.follow_up_due_ids(NOW + timedelta(days=5)) == {job().id}
    assert db.follow_up_due_ids(NOW) == set()


def test_list_runs_newest_first(db):
    first = db.start_run(Run(started_at=NOW))
    second = db.start_run(Run(started_at=NOW + timedelta(days=1)))
    assert [i for i, _ in db.list_runs()] == [second, first]


def test_file_db_uses_wal(tmp_path):
    d = DB(tmp_path / "x.db")
    assert d.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
