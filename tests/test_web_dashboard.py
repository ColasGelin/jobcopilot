from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.db import DB
from jobcopilot.models import Evaluation, Run
from jobcopilot.web import create_app
from jobcopilot.web.insights import dashboard, next_step, run_warning, steps

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def row(n=1, score=70, status="new", prep=None, applied_at=None, source="hn", day="2026-11-02", reason=None):
    return {"id": str(n), "company": f"Co{n}", "title": "Engineer", "source": source, "day": day, "score": score,
            "status": status, "applied_at": applied_at, "prep": prep, "reason": reason}


def stats(rows, follow_ups=()):
    return dashboard(rows, date(2026, 11, 2), 60, set(follow_ups))


@pytest.mark.parametrize("status,prep,expected", [
    ("new", None, "prepare"),
    ("shortlisted", None, "prepare"),
    ("new", "ready", "review"),
    ("new", "needs_you", "review"),
    ("new", "filled", "apply"),
    ("new", "reading", None),
    ("applied", "ready", None),
    ("interview", None, None),
    ("skipped", None, None),
    ("rejected", "filled", None),
    ("unavailable", None, None),
])
def test_next_step(status, prep, expected):
    assert next_step(status, prep) == expected


def test_steps_mark_what_is_done_and_what_needs_attention():
    states = {s["label"]: s["state"] for s in steps("new", True, "needs_you", False)}
    assert states == {"CV": "done", "Application": "attention", "Cover letter": "todo", "Applied": "todo"}
    states = {s["label"]: s["state"] for s in steps("interview", False, "applied", True)}
    assert states == {"CV": "todo", "Application": "done", "Cover letter": "done", "Applied": "done"}


def test_steps_stop_asking_for_attention_once_a_job_is_closed():
    assert steps("applied", False, "needs_you", False)[1]["state"] == "done"
    assert steps("skipped", False, "needs_you", False)[1]["state"] == "todo"


def test_funnel_stages_never_grow():
    rows = [row(1, score=40), row(2, score=65), row(3, score=85, prep="ready"),
            row(4, score=30, status="interview", applied_at="2026-11-01"),
            row(5, status="rejected", applied_at="2026-11-01"), row(6, status="offer")]
    funnel = {f["label"]: f["count"] for f in stats(rows)["funnel"]}
    assert funnel == {"Found": 6, "Matched": 5, "Prepared": 4, "Applied": 3, "Interview": 2, "Offer": 1}


def test_key_numbers():
    rows = [row(1, score=80, prep="ready"), row(2, score=79, prep="filled"), row(3, score=90, status="skipped", prep="ready"),
            row(4, status="applied", applied_at="2026-11-01"), row(5, status="interview", applied_at="2026-11-01")]
    assert stats(rows)["kpis"] == {"found": 5, "strong": 2, "ready": 2, "applied": 2, "interviews": 1}


def test_days_cover_the_last_two_weeks_and_ignore_older_offers():
    days = stats([row(1, day="2026-11-02"), row(2, day="2026-11-02"), row(3, day="2026-10-20"), row(4, day="2026-10-19")])["days"]
    assert len(days) == 14 and days[0]["day"] == "2026-10-20" and days[-1]["day"] == "2026-11-02"
    assert days[0]["count"] == 1 and days[-1]["count"] == 2


def test_score_bands_include_their_edges():
    bands = stats([row(i, score=s) for i, s in enumerate([0, 39, 40, 59, 60, 69, 70, 79, 80, 100])])["scores"]
    assert [b["count"] for b in bands] == [2, 2, 2, 2, 2]


def test_sources_are_named_and_folded_after_six():
    rows = [row(i, source=f"s{i}") for i in range(7)] + [row(9, source="s0"), row(10, source="manual"), row(11, source="linkedin")]
    sources = stats(rows)["sources"]
    assert sources[0] == {"label": "s0", "count": 2}
    assert sources[-1] == {"label": "Other", "count": 3} and len(sources) == 7
    assert {"label": "LinkedIn", "count": 1} in stats([row(1, source="linkedin")])["sources"]


def test_todo_is_ranked_by_score_and_leaves_out_closed_jobs():
    rows = [row(1, score=50, prep="ready"), row(2, score=90, prep="needs_you", reason="Sign-in"), row(3, score=99, prep="ready", status="skipped"),
            row(4, score=75, status="applied", applied_at="2026-10-20"), row(5, score=70, prep="filled"), row(6, score=70, prep="needs_you", reason="x")]
    todo = stats(rows, follow_ups={"4"})["todo"]
    assert [t["id"] for t in todo["items"]] == ["2", "4", "5", "6", "1"] and todo["total"] == 5  # a tie puts ready first
    assert todo["items"][0]["what"] == "Needs you: Sign-in"
    assert todo["items"][2]["href"] == "/jobs/5/application"
    assert todo["items"][1]["href"] == "/today?day=2026-11-02&all=1#job-4"


def test_todo_is_capped_but_keeps_the_total():
    todo = stats([row(i, prep="ready") for i in range(12)])["todo"]
    assert len(todo["items"]) == 8 and todo["total"] == 12


def test_no_jobs_gives_empty_stats():
    s = stats([])
    assert s["kpis"]["found"] == 0 and s["todo"]["items"] == [] and s["sources"] == []


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "jobs.db")


def seed(db, job, score=80):
    db.insert_jobs([job], NOW)
    db.save_evaluation(Evaluation(job_id=job.id, score=score, fit_reasons="fits", gaps="gap", model="m", prompt_version="v"))


def client(db):
    path = db.conn.execute("PRAGMA database_list").fetchone()[2]
    return TestClient(create_app(path, clock=lambda: NOW), base_url="http://localhost", headers={"HX-Request": "true"})


def test_dashboard_rows_use_the_latest_score(db):
    job = make_job(1)
    seed(db, job, 40)
    db.save_evaluation(Evaluation(job_id=job.id, score=85, fit_reasons="", gaps="", model="m", prompt_version="v"))
    db.create_prepared(job.id, "needs_you", NOW, reason="Sign-in")
    r = db.dashboard_rows()[0]
    assert (r["score"], r["status"], r["prep"], r["reason"], r["day"]) == (85, "new", "needs_you", "Sign-in", "2026-11-02")


def test_home_is_the_dashboard(db):
    job = make_job(1, company="Acme")
    seed(db, job, 85)
    db.create_prepared(job.id, "ready", NOW)
    rid = db.start_run(Run(started_at=NOW))
    db.finish_run(rid, Run(started_at=NOW, finished_at=NOW, fetched=578, new=12, failed_sources=["remoteok"]))
    body = client(db).get("/").text
    for text in ("Dashboard", "Strong matches", "Found", "Acme", f"/jobs/{job.id}/application", "578", "remoteok"):
        assert text in body


def test_skip_from_the_todo_list(db):
    job, applied = make_job(1, company="Acme"), make_job(2, company="FollowCo")
    seed(db, job, 85)
    seed(db, applied, 90)
    db.create_prepared(job.id, "ready", NOW)
    db.set_status(applied.id, "applied", NOW - timedelta(days=10))  # a follow-up is due
    c = client(db)
    body = c.get("/").text
    assert f'hx-post="/jobs/{job.id}/status?page=1"' in body
    assert f'hx-post="/jobs/{applied.id}/status?page=1"' not in body  # skipping would erase "applied"
    r = c.post(f"/jobs/{job.id}/status?page=1", data={"status": "skipped"})
    assert r.status_code == 200 and r.headers["HX-Refresh"] == "true"
    assert db.status(job.id) == "skipped"
    body = c.get("/").text
    assert f"/jobs/{job.id}/application" not in body and "FollowCo" in body


def test_dashboard_empty_state(db):
    r = client(db).get("/")
    assert r.status_code == 200 and "No offers yet" in r.text


def test_dashboard_escapes_feed_content(db):
    job = make_job(1, company="<script>alert(1)</script>")
    seed(db, job)
    db.create_prepared(job.id, "ready", NOW)
    body = client(db).get("/").text
    assert "&lt;script&gt;alert(1)" in body and "<script>alert(1)" not in body


def test_today_filters(db):
    strong = make_job(1, company="StrongCo", remote=False)
    mid = make_job(2, company="MidCo", remote=True)
    seed(db, strong, 85)
    seed(db, mid, 65)
    db.create_prepared(mid.id, "ready", NOW)
    c = client(db)
    assert "StrongCo" in c.get("/today").text and "MidCo" in c.get("/today").text
    assert "MidCo" not in c.get("/today?show=strong").text
    assert "StrongCo" not in c.get("/today?show=remote").text
    assert "StrongCo" not in c.get("/today?show=ready").text
    assert "StrongCo" in c.get("/today?show=nonsense").text


def test_today_does_not_link_to_a_future_day(db):
    seed(db, make_job(1))
    c = client(db)
    assert "2026-11-03" not in c.get("/today?day=2026-11-02").text
    assert 'href="/today?day=2026-11-02"' in c.get("/today?day=2026-11-01").text


def test_card_leads_with_the_next_step(db):
    job = make_job(1)
    seed(db, job)
    c = client(db)
    assert "Prepare application" in c.get("/today").text
    app = db.create_prepared(job.id, "ready", NOW)
    assert "Review answers" in c.get("/today").text
    db.update_prepared(app, NOW, status="needs_you", reason="Sign-in required")
    assert "Finish it yourself" in c.get("/today").text
    db.update_prepared(app, NOW, status="filled")
    assert f'hx-post="/jobs/{job.id}/applied"' in c.get(f"/jobs/{job.id}/progress").text


def test_progress_fragment_has_no_review_button_on_the_application_page(db):
    job = make_job(1)
    seed(db, job)
    db.create_prepared(job.id, "ready", NOW)
    assert "Review answers" not in client(db).get(f"/jobs/{job.id}/progress?page=1").text
    assert client(db).get("/jobs/nope/progress").status_code == 404


def test_preparing_tells_the_progress_strip_to_refresh(db):
    job = make_job(1)
    seed(db, job)
    r = client(db).post(f"/jobs/{job.id}/prepare")
    assert r.headers["HX-Trigger"] == f"job-changed-{job.id}"


def test_marking_applied_from_the_card_updates_the_application_too(db):
    job = make_job(1)
    seed(db, job)
    app = db.create_prepared(job.id, "filled", NOW)
    client(db).post(f"/jobs/{job.id}/status", data={"status": "applied"})
    assert db.get_prepared(app).status == "applied"


@pytest.mark.parametrize("last,now,expected", [
    (date(2026, 11, 2), datetime(2026, 11, 2, 9, 0), None),
    (date(2026, 11, 1), datetime(2026, 11, 2, 8, 0), None),       # today's run is not due yet
    (date(2026, 11, 1), datetime(2026, 11, 2, 10, 30), "No daily run today yet (last one 01 Nov)."),
    (date(2026, 10, 28), datetime(2026, 11, 2, 7, 0), "No daily run since 28 Oct."),
    (None, datetime(2026, 11, 2, 12, 0), "No daily run has finished yet."),
])
def test_run_warning(last, now, expected):
    assert run_warning(last, now) == expected


def test_dashboard_shows_the_run_warning(db):
    seed(db, make_job(1))
    rid = db.start_run(Run(started_at=NOW - timedelta(days=3)))
    db.finish_run(rid, Run(started_at=NOW - timedelta(days=3), finished_at=NOW - timedelta(days=3), fetched=1))
    assert "No daily run since 30 Oct." in client(db).get("/").text


@pytest.mark.parametrize("url,site", [
    ("https://www.linkedin.com/jobs/view/1", "LinkedIn"), ("https://es.linkedin.com/jobs/view/1", "LinkedIn"),
    ("https://jobs.ashbyhq.com/alan/d4762f23", "Ashby"), ("https://job-boards.eu.greenhouse.io/x/jobs/1", "Greenhouse"),
    ("https://grnh.se/abc", "Greenhouse"), ("https://jobs.eu.lever.co/x/1", "Lever"), ("https://apply.workable.com/j/1", "Workable"),
    ("https://speedcast.wd1.myworkdayjobs.com/en-US/x", "Workday"), ("https://news.ycombinator.com/item?id=1", "Hacker News"),
    ("https://himalayas.app/companies/x/jobs/y", "Himalayas"), ("https://job.tecdata.es/jobs/1-x", "Company site"),
    ("https://notlinkedin.com/jobs/1", "Company site"),  # only the real domain or its subdomains
    ("", "Company site"),
])
def test_page_site(url, site):
    from jobcopilot.web.insights import page_site
    assert page_site(url) == site


def test_offer_cards_show_the_site_of_the_page_and_the_feed_that_found_it(db):
    ashby = make_job(1, company="AshbyCo", source="indeed", url="https://jobs.ashbyhq.com/acme/1")
    linkedin = make_job(2, company="LinkCo", source="linkedin", url="https://www.linkedin.com/jobs/view/2")
    seed(db, ashby, 85)
    seed(db, linkedin, 80)
    db.set_status(ashby.id, "shortlisted", NOW)
    c = client(db)
    for page in ("/offers", "/pipeline"):
        body = c.get(page).text
        assert 'title="Found via Indeed · https://jobs.ashbyhq.com/acme/1">Ashby ↗</a>' in body, page
    body = c.get("/offers").text
    assert 'title="https://www.linkedin.com/jobs/view/2">LinkedIn ↗</a>' in body  # same site as its feed: no "found via"
