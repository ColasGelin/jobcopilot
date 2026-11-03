import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jobcopilot.apply.models import Answer, FormField, FormSnapshot
from jobcopilot.db import DB
from jobcopilot.models import Evaluation, Job

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    return DB(":memory:")


def add_job(db: DB, i: int, score: int | None = 80) -> Job:
    job = Job(url=f"https://jobs/{i}", source="s", company=f"Co{i}", title=f"Engineer {i}")
    db.insert_jobs([job], NOW)
    if score is not None:
        db.save_evaluation(Evaluation(job_id=job.id, score=score, fit_reasons="", gaps="", model="m", prompt_version="v"))
    return job


def answers():
    return [Answer(field_key="q1", label="Why us?", kind="open", draft="I built..."),
            Answer(field_key="email", label="Email", kind="fact", profile_key="email", draft="a@b.c")]


def test_prepared_app_lifecycle(db):
    job = add_job(db, 1)
    app = db.create_prepared(job.id, "reading", NOW)
    assert db.prepared_for_job(job.id).status == "reading" and db.get_prepared(app).job_id == job.id
    form = FormSnapshot(url="https://jobs/1", fields=[FormField(key="q1", selector="#q1", label="Why us?", kind="textarea")])
    db.update_prepared(app, NOW, status="ready", form=form, ai_ban_quote="No AI.")
    got = db.get_prepared(app)
    assert (got.status, got.form, got.ai_ban_quote, got.reason) == ("ready", form, "No AI.", None)
    db.update_prepared(app, NOW, status="needs_you", reason="Sign-in")
    got = db.get_prepared(app)
    assert (got.status, got.reason, got.form, got.ai_ban_quote) == ("needs_you", "Sign-in", form, "No AI.")
    assert db.get_prepared(999) is None and db.prepared_for_job("nope") is None


def test_one_prepared_app_per_job(db):
    job = add_job(db, 1)
    db.create_prepared(job.id, "reading", NOW)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_prepared(job.id, "reading", NOW)


def test_answers_final_and_voice_examples(db):
    job = add_job(db, 1)
    app = db.create_prepared(job.id, "ready", NOW)
    db.save_answers(app, answers(), "sonnet", "apply_v1", NOW)
    saved = db.answers(app)
    assert [a.field_key for a in saved] == ["q1", "email"]
    assert saved[0].final == saved[0].draft == "I built..." and saved[0].id is not None
    assert db.set_final(saved[0].id, "Mine", NOW) and db.answers(app)[0].final == "Mine"
    assert not db.set_final(999, "x", NOW)
    assert db.voice_examples() == []  # app still "ready"
    db.update_prepared(app, NOW, status="filled")
    assert db.voice_examples() == [("Why us?", "Mine")]


def test_jobs_to_prepare_filters_and_orders(db):
    high, mid, low = add_job(db, 1, 90), add_job(db, 2, 76), add_job(db, 3, 60)
    skipped, done, unscored = add_job(db, 4, 95), add_job(db, 5, 99), add_job(db, 6, None)
    db.set_status(skipped.id, "skipped", NOW)
    db.create_prepared(done.id, "ready", NOW)
    assert db.jobs_to_prepare(75, 10) == [high.id, mid.id]
    assert db.jobs_to_prepare(75, 1) == [high.id]


def test_take_next_command_order_and_stale_retake(db):
    app = db.create_prepared(add_job(db, 1).id, "ready", NOW)
    c1, c2 = db.queue_command(app, "fill", NOW), db.queue_command(app, "read", NOW)
    assert db.take_next_command(NOW).id == c1 and db.take_next_command(NOW).id == c2
    assert db.take_next_command(NOW) is None
    assert db.take_next_command(NOW + timedelta(minutes=11)).id == c1  # stale taken is retried
    db.set_command(c2, "waiting", NOW)
    assert db.take_next_command(NOW + timedelta(minutes=30)).id == c1  # waiting is never retaken
    db.set_command(c1, "done", NOW, result={"filled": ["q1"], "unfilled": []})
    got = db.get_command(c1)
    assert (got.status, got.result["filled"]) == ("done", ["q1"])
    assert db.latest_command(app).id == c2 and db.get_command(999) is None


def test_delete_jobs_removes_prepared_rows(db):
    job = add_job(db, 1)
    app = db.create_prepared(job.id, "ready", NOW)
    db.save_answers(app, answers(), "m", "v", NOW)
    db.queue_command(app, "fill", NOW)
    db.delete_jobs([job.id])
    assert db.get_prepared(app) is None and db.answers(app) == [] and db.take_next_command(NOW) is None
