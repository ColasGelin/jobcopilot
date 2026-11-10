from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.db import DB
from jobcopilot.models import Evaluation
from jobcopilot.web import create_app

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


def seed(db, job, score, seen=NOW):
    db.insert_jobs([job], seen)
    db.save_evaluation(Evaluation(job_id=job.id, score=score, fit_reasons="fits", gaps="gap", model="m", prompt_version="v"))


def client(tmp_path):
    return TestClient(create_app(tmp_path / "jobs.db", clock=lambda: NOW), base_url="http://localhost", headers={"HX-Request": "true"})


def test_open_offers_cover_every_day_best_first(tmp_path):
    db = DB(tmp_path / "jobs.db")
    seed(db, make_job(1, company="OldCo"), 70, NOW - timedelta(days=5))
    seed(db, make_job(2, company="NewCo"), 90)
    seed(db, make_job(3, company="WeakCo"), 40)
    skipped = make_job(4, company="SkippedCo")
    seed(db, skipped, 95)
    db.set_status(skipped.id, "skipped", NOW)
    assert db.open_job_ids(60) == [make_job(2).id, make_job(1).id]
    body = client(tmp_path).get("/offers").text
    assert body.index("NewCo") < body.index("OldCo") and "WeakCo" not in body and "SkippedCo" not in body


def test_offers_search_and_filters(tmp_path):
    db = DB(tmp_path / "jobs.db")
    seed(db, make_job(1, company="Acme", title="Python Developer", remote=False, location="Málaga"), 70)
    seed(db, make_job(2, company="Beta", title="ML Engineer"), 85)
    c = client(tmp_path)
    assert "Acme" in c.get("/offers?q=python").text and "Beta" not in c.get("/offers?q=python").text
    assert "Beta" in c.get("/offers?q=BETA").text
    assert "Acme" not in c.get("/offers?show=strong").text
    assert "Acme" not in c.get("/offers?show=remote").text
    assert "No open offers" in c.get("/offers?q=nothing-matches").text


def test_offers_are_capped_with_a_link_for_more(tmp_path):
    db = DB(tmp_path / "jobs.db")
    for i in range(35):
        seed(db, make_job(i), 70)
    body = client(tmp_path).get("/offers").text
    assert body.count('class="card ') == 30 and "Show 5 more" in body
    assert client(tmp_path).get("/offers?limit=60").text.count('class="card ') == 35


def test_applications_list_newest_first_with_days_and_follow_up(tmp_path):
    db = DB(tmp_path / "jobs.db")
    old, new, open_ = make_job(1, company="OldCo"), make_job(2, company="NewCo"), make_job(3, company="OpenCo")
    for j in (old, new, open_):
        seed(db, j, 70)
    db.set_status(old.id, "applied", NOW - timedelta(days=8))
    db.set_status(new.id, "applied", NOW - timedelta(days=1))
    db.set_status(new.id, "interview", NOW)
    rows = db.applications_list()
    assert [r["company"] for r in rows] == ["NewCo", "OldCo"]
    body = client(tmp_path).get("/applied").text
    assert body.index("NewCo") < body.index("OldCo") and "OpenCo" not in body
    assert "8 days ago" in body and "Follow up" in body and "Interview" in body


def test_applications_empty_state(tmp_path):
    DB(tmp_path / "jobs.db")
    assert "No applications yet" in client(tmp_path).get("/applied").text


def test_nav_links_the_new_pages(tmp_path):
    DB(tmp_path / "jobs.db")
    body = client(tmp_path).get("/").text
    assert 'href="/offers"' in body and 'href="/applied"' in body
