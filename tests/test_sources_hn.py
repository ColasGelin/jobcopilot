import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from jobcopilot.sources import build_sources, http_client
from jobcopilot.sources.hn import HNSource

FIX = Path(__file__).parent / "fixtures"
SINCE = datetime(2026, 10, 30, tzinfo=timezone.utc)


def hn_client(seen: list[str] | None = None) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(req.url))
        name = "hn_search.json" if "search_by_date" in req.url.path else "hn_item.json"
        return httpx.Response(200, content=(FIX / name).read_bytes(), headers={"content-type": "application/json"})
    return http_client(httpx.MockTransport(handler))


def jobs_by_url():
    return {j.url: j for j in HNSource(hn_client()).fetch(SINCE)}


def test_hn_picks_who_is_hiring_thread():
    urls: list[str] = []
    HNSource(hn_client(urls)).fetch(SINCE)
    assert urls[-1].endswith("/items/49922569")  # October "Who is hiring?", not "Who wants to be hired?"


def test_hn_parses_pipe_header():
    j = jobs_by_url()["https://news.ycombinator.com/item?id=1001"]
    assert (j.company, j.title) == ("Acme AI", "ML Engineer")
    assert "REMOTE (EU)" in j.location
    assert j.remote
    assert "Python & LangGraph" in j.description


def test_hn_parses_em_dash_header():
    j = jobs_by_url()["https://news.ycombinator.com/item?id=1002"]
    assert j.company == "PrairieLearn (Remote US)"
    assert j.title == "Full-Stack Software Engineer"


def test_hn_comment_without_separator_uses_first_words_as_company():
    j = jobs_by_url()["https://news.ycombinator.com/item?id=1003"]
    assert j.company == "We are hiring backend engineers"
    assert j.title == "See posting"


def test_hn_skips_deleted_and_nested_comments():
    urls = set(jobs_by_url())
    assert len(urls) == 4
    assert not any(u.endswith(("1004", "2001")) for u in urls)


def test_hn_keeps_whole_current_thread_regardless_of_since():
    assert len(jobs_by_url()) == 4  # comments are from Oct 1, SINCE is Oct 30


def test_build_sources_knows_hn():
    assert [s.name for s in build_sources(["hn"], hn_client())] == ["hn"]


def test_hn_skips_url_fields_in_header():
    j = jobs_by_url()["https://news.ycombinator.com/item?id=1005"]
    assert (j.company, j.title) == ("Checkly", "Product Engineer")
    assert j.location == "REMOTE (Europe)"
