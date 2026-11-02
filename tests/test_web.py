from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.db import DB
from jobcopilot.models import Draft, Evaluation, Run
from jobcopilot.web import create_app

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "jobs.db"


def seed(db_path, jobs_scores, seen_at=NOW, draft_for=()):
    db = DB(db_path)
    jobs = [j for j, _ in jobs_scores]
    db.insert_jobs(jobs, seen_at)
    for j, score in jobs_scores:
        db.save_evaluation(Evaluation(job_id=j.id, score=score, fit_reasons=f"fit {j.company}", gaps="gap", model="m", prompt_version="v"))
    for j in draft_for:
        db.save_draft(Draft(job_id=j.id, cv_tweaks=["tweak"], cover_note=f"COVER {j.company}", outreach_message="HELLO", contact_hint="CTO", model="m", prompt_version="v"))
    return db


def client(db_path, now=NOW):
    return TestClient(create_app(db_path, clock=lambda: now), base_url="http://localhost", headers={"HX-Request": "true"})


def test_today_shows_matches_sorted(db_path):
    low, high = make_job(1, company="LowCo"), make_job(2, company="HighCo")
    seed(db_path, [(low, 61), (high, 90)], draft_for=[high])
    body = client(db_path).get("/").text
    assert body.index("HighCo") < body.index("LowCo")
    assert "COVER HighCo" in body and "No draft" in body


def test_today_empty_state(db_path):
    r = client(db_path).get("/")
    assert r.status_code == 200 and "No matches yet" in r.text


def test_today_day_param(db_path):
    old, new = make_job(1, company="OldCo"), make_job(2, company="NewCo")
    seed(db_path, [(old, 70)], seen_at=NOW - timedelta(days=1))
    seed(db_path, [(new, 70)])
    body = client(db_path).get("/?day=2026-11-01").text
    assert "OldCo" in body and "NewCo" not in body


def test_status_change_returns_card_and_persists(db_path):
    j = make_job(1, company="Acme")
    seed(db_path, [(j, 80)])
    r = client(db_path).post(f"/jobs/{j.id}/status", data={"status": "applied"})
    assert r.status_code == 200 and "Acme" in r.text and "applied" in r.text
    assert DB(db_path).status(j.id) == "applied"


def test_status_unknown_job_404(db_path):
    DB(db_path)
    assert client(db_path).post("/jobs/nope/status", data={"status": "applied"}).status_code == 404


def test_status_invalid_400(db_path):
    j = make_job(1)
    seed(db_path, [(j, 80)])
    assert client(db_path).post(f"/jobs/{j.id}/status", data={"status": "ghosted"}).status_code == 400
    assert DB(db_path).status(j.id) == "new"


def test_feed_content_is_escaped(db_path):
    j = make_job(1, title="<script>alert(1)</script>")
    seed(db_path, [(j, 80)])
    body = client(db_path).get("/").text
    assert "&lt;script&gt;alert(1)" in body and "<script>alert(1)" not in body


def test_htmx_served_locally(db_path):
    r = client(db_path).get("/static/htmx.min.js")
    assert r.status_code == 200 and "htmx" in r.text


def test_pipeline_groups_and_flags_follow_up(db_path):
    applied, interview = make_job(1, company="AppliedCo"), make_job(2, company="InterviewCo")
    db = seed(db_path, [(applied, 70), (interview, 80)])
    db.set_status(applied.id, "applied", NOW - timedelta(days=6))
    db.set_status(interview.id, "interview", NOW)
    body = client(db_path).get("/pipeline").text
    assert "AppliedCo" in body and "InterviewCo" in body
    assert "Follow up" in body
    assert body.index("<h2>Applied (1)</h2>") < body.index("AppliedCo") < body.index("<h2>Interview (1)</h2>")


def test_pipeline_empty_state(db_path):
    DB(db_path)
    r = client(db_path).get("/pipeline")
    assert r.status_code == 200 and "Nothing here yet" in r.text


def test_runs_lists_failed_sources_and_errors(db_path):
    db = DB(db_path)
    rid = db.start_run(Run(started_at=NOW))
    db.finish_run(rid, Run(started_at=NOW, finished_at=NOW + timedelta(seconds=95), fetched=578, new=12, kept=5,
                           failed_sources=["remoteok"], error="Claude Code is logged out"))
    body = client(db_path).get("/runs").text
    assert "578" in body and "remoteok" in body and "logged out" in body and "1m 35s" in body


def test_runs_empty_state(db_path):
    DB(db_path)
    r = client(db_path).get("/runs")
    assert r.status_code == 200 and "No runs yet" in r.text


def test_status_change_from_pipeline_returns_compact_card(db_path):
    j = make_job(1, company="Acme")
    seed(db_path, [(j, 80)])
    body = client(db_path).post(f"/jobs/{j.id}/status?compact=1", data={"status": "applied"}).text
    assert "compact" in body and "Fit:" not in body


HX = {"HX-Request": "true"}


def test_javascript_url_is_not_rendered_as_link(db_path):
    j = make_job(1, url="javascript:alert(1)")
    seed(db_path, [(j, 80)])
    body = client(db_path).get("/").text
    assert 'href="javascript:' not in body


def test_foreign_host_header_rejected(db_path):
    DB(db_path)
    r = client(db_path).get("/", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


def test_post_without_htmx_header_rejected(db_path):
    j = make_job(1)
    seed(db_path, [(j, 80)])
    r = TestClient(create_app(db_path, clock=lambda: NOW), base_url="http://localhost").post(f"/jobs/{j.id}/status", data={"status": "skipped"})
    assert r.status_code == 403
    assert DB(db_path).status(j.id) == "new"


@pytest.mark.parametrize("day", ["foo", "2026-13-01", "0001-01-01", "9999-12-31"])
def test_bad_day_param_shows_page(db_path, day):
    j = make_job(1, company="Acme")
    seed(db_path, [(j, 80)])
    r = client(db_path).get(f"/?day={day}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_offer_button_exists(db_path):
    j = make_job(1)
    seed(db_path, [(j, 80)])
    assert '"status": "offer"' in client(db_path).get("/").text


def test_today_hides_jobs_below_min_score(db_path):
    good, weak = make_job(1, company="GoodCo"), make_job(2, company="WeakCo")
    seed(db_path, [(good, 60), (weak, 59)])
    body = client(db_path).get("/").text
    assert "GoodCo" in body and "WeakCo" not in body
    assert "Show 1 lower-scored job" in body


def test_today_all_param_shows_hidden_jobs(db_path):
    weak = make_job(2, company="WeakCo")
    seed(db_path, [(weak, 40)])
    assert "WeakCo" in client(db_path).get("/?all=1").text


def test_min_score_is_configurable(db_path):
    j = make_job(1, company="MidCo")
    seed(db_path, [(j, 70)])
    app = create_app(db_path, clock=lambda: NOW, min_score=75)
    assert "MidCo" not in TestClient(app, base_url="http://localhost").get("/").text
