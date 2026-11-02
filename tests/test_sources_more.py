from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from jobcopilot.config import WatchlistEntry
from jobcopilot.sources import SourceError, build_sources, http_client
from jobcopilot.sources.himalayas import HimalayasSource
from jobcopilot.sources.jobicy import JobicySource
from jobcopilot.sources.watchlist import WatchlistSource
from jobcopilot.sources.workingnomads import WorkingNomadsSource
from jobcopilot.sources.wwr import WWRSource

FIX = Path(__file__).parent / "fixtures"
SINCE = datetime(2026, 10, 30, tzinfo=timezone.utc)
ALL = datetime(2020, 1, 1, tzinfo=timezone.utc)


def serving(routes: dict[str, str], seen: list | None = None) -> httpx.Client:
    """Serve fixture files by URL substring; anything else is a 404."""
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(req.url))
        for needle, name in routes.items():
            if needle in str(req.url):
                ctype = "application/rss+xml" if name.endswith(".xml") else "application/json"
                return httpx.Response(200, content=(FIX / name).read_bytes(), headers={"content-type": ctype})
        return httpx.Response(404, content=b"not found")
    return http_client(httpx.MockTransport(handler))


# --- Jobicy -----------------------------------------------------------------

def test_jobicy_parses_and_filters_by_date():
    jobs = JobicySource(serving({"jobicy.com": "jobicy.json"})).fetch(SINCE)
    assert len(jobs) == 2
    j = jobs[0]
    assert (j.source, j.company, j.remote) == ("jobicy", "QuickNode", True)
    assert j.location == "Portugal, USA"
    assert j.salary_text == "171,000 - 214,000 USD"
    assert "<" not in j.description


def test_jobicy_requests_europe_dev_jobs():
    seen: list[str] = []
    JobicySource(serving({"jobicy.com": "jobicy.json"}, seen)).fetch(ALL)
    assert "geo=europe" in seen[0] and "industry=dev" in seen[0]


# --- Himalayas --------------------------------------------------------------

def test_himalayas_searches_each_query_in_spain():
    seen: list[str] = []
    src = HimalayasSource(serving({"himalayas.app": "himalayas.json"}, seen), queries=["python", "AI engineer"], country="Spain")
    jobs = src.fetch(ALL)
    assert len(seen) == 2 and all("country=Spain" in u for u in seen)
    assert len(jobs) == 3  # same postings for both queries are de-duplicated by URL


def test_himalayas_location_mapping():
    jobs = {j.company: j for j in HimalayasSource(serving({"himalayas.app": "himalayas.json"}), ["python"], "Spain").fetch(ALL)}
    assert jobs["micro1"].location == "" and jobs["micro1"].remote          # worldwide
    assert jobs["lemon.io"].location == "Spain"                              # long list containing Spain
    assert jobs["Logicalis Spain"].location == "Spain"


def test_himalayas_drops_old_jobs():
    assert len(HimalayasSource(serving({"himalayas.app": "himalayas.json"}), ["python"], "Spain").fetch(SINCE)) == 2


# --- Working Nomads ---------------------------------------------------------

def test_workingnomads_keeps_development_only():
    jobs = WorkingNomadsSource(serving({"workingnomads.com": "workingnomads.json"})).fetch(ALL)
    assert [j.company for j in jobs] == ["Lemon.io", "Lemon.io"]
    assert jobs[0].location.startswith("Europe") and jobs[0].remote


def test_workingnomads_drops_old_jobs():
    assert len(WorkingNomadsSource(serving({"workingnomads.com": "workingnomads.json"})).fetch(SINCE)) == 2


# --- We Work Remotely -------------------------------------------------------

def test_wwr_parses_rss_company_and_title():
    jobs = WWRSource(serving({"weworkremotely.com": "wwr.xml"})).fetch(ALL)
    assert len(jobs) == 3
    assert (jobs[0].company, jobs[0].title) == ("Databricks", "Deployment Strategist")
    assert jobs[0].location == "Anywhere in the World" and jobs[0].remote
    assert jobs[0].url.startswith("https://weworkremotely.com/remote-jobs/")
    assert "<" not in jobs[0].description


def test_wwr_drops_old_items():
    assert len(WWRSource(serving({"weworkremotely.com": "wwr.xml"})).fetch(SINCE)) == 2


def test_wwr_garbage_raises_source_error():
    client = http_client(httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>nope")))
    with pytest.raises(SourceError):
        WWRSource(client).fetch(ALL)


# --- Watchlist (Greenhouse / Ashby / Lever) ----------------------------------

ROUTES = {"greenhouse.io/v1/boards/globex": "greenhouse.json", "ashbyhq.com/posting-api/job-board/hooli": "ashby.json",
          "lever.co/v0/postings/initech": "lever.json"}
WATCH = [WatchlistEntry(name="Globex", ats="greenhouse", slug="globex"), WatchlistEntry(name="hooli", ats="ashby", slug="hooli"),
         WatchlistEntry(name="Initech", ats="lever", slug="initech")]


def test_watchlist_fetches_every_company_and_uses_configured_name():
    jobs = WatchlistSource(serving(ROUTES), WATCH).fetch(SINCE)
    by_company = {}
    for j in jobs:
        by_company.setdefault(j.company, []).append(j)
    assert {k: len(v) for k, v in by_company.items()} == {"Globex": 3, "hooli": 3, "Initech": 2}
    assert all(j.source == "watchlist" for j in jobs)


def test_watchlist_greenhouse_fields():
    j = next(j for j in WatchlistSource(serving(ROUTES), WATCH[:1]).fetch(ALL) if j.location == "Madrid")
    assert j.url.startswith("https://job-boards.eu.greenhouse.io/globex/jobs/")
    assert not j.remote and "<" not in j.description and j.posted_at is not None


def test_watchlist_ashby_joins_secondary_locations_and_remote():
    j = WatchlistSource(serving(ROUTES), WATCH[1:2]).fetch(ALL)[0]
    assert j.remote and "Spain" in j.location and j.location.startswith("Berlin Office")


def test_watchlist_lever_fields():
    j = WatchlistSource(serving(ROUTES), WATCH[2:]).fetch(ALL)[0]
    assert j.remote and "Spain" in j.location and j.url.startswith("https://jobs.lever.co/initech/")


def test_watchlist_one_broken_company_does_not_fail_the_rest():
    src = WatchlistSource(serving(ROUTES), WATCH + [WatchlistEntry(name="Ghost", ats="greenhouse", slug="ghost")])
    jobs = src.fetch(ALL)
    assert len(jobs) == 8
    assert src.errors == ["watchlist:Ghost"]


def test_build_sources_knows_new_sources():
    from jobcopilot.config import Settings
    settings = Settings(data_dir=Path("."), watchlist=WATCH)
    names = ["jobicy", "himalayas", "workingnomads", "wwr", "watchlist"]
    assert [s.name for s in build_sources(names, serving({}), settings)] == names
