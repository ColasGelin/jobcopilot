from datetime import datetime, timezone

import pandas as pd

from jobcopilot.config import LinkedInSearch, load_settings
from jobcopilot.sources import build_sources
from jobcopilot.sources.linkedin import LinkedInSource

SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc)


def frame(rows):
    base = {"job_url": "", "job_url_direct": None, "title": "", "company": "", "location": "", "date_posted": None,
            "is_remote": False, "description": "", "min_amount": float("nan"), "max_amount": float("nan"),
            "currency": None, "interval": None}
    return pd.DataFrame([base | r for r in rows])


def test_rows_become_jobs():
    calls = []

    def scrape(**kw):
        calls.append(kw)
        return frame([
            {"job_url": "https://www.linkedin.com/jobs/view/1", "title": "AI Engineer", "company": "Acme",
             "location": "Málaga, Andalusia, Spain", "date_posted": "2026-11-03", "description": "Build **agents**.",
             "min_amount": 40000.0, "max_amount": 55000.0, "currency": "EUR", "interval": "yearly"},
            {"job_url": "https://www.linkedin.com/jobs/view/2", "job_url_direct": "https://jobs.lever.co/acme/abc",
             "title": "LLM Engineer", "company": "Beta", "location": "Spain", "is_remote": True},
        ])

    src = LinkedInSource([LinkedInSearch(term="AI engineer", location="Málaga, Spain")], results=25, hours_old=48, scrape=scrape)
    jobs = src.fetch(SINCE)
    assert calls == [{"site_name": ["linkedin"], "search_term": "AI engineer", "location": "Málaga, Spain", "results_wanted": 25,
                      "hours_old": 48, "fetch_description": True, "verbose": 0}]
    first, second = jobs
    assert (first.source, first.url, first.company, first.title, first.location) == (
        "linkedin", "https://www.linkedin.com/jobs/view/1", "Acme", "AI Engineer", "Málaga, Andalusia, Spain")
    assert first.salary_text == "40,000 - 55,000 EUR / yearly" and first.posted_at.date().isoformat() == "2026-11-03"
    assert "agents" in first.description and not first.remote
    assert second.url == "https://jobs.lever.co/acme/abc" and second.remote and second.salary_text is None
    assert src.errors == []


def test_failed_search_is_reported_and_others_continue():
    def scrape(**kw):
        if kw["search_term"] == "bad":
            raise RuntimeError("429 Too Many Requests")
        return frame([{"job_url": "https://www.linkedin.com/jobs/view/3", "title": "T", "company": "C"}])

    src = LinkedInSource([LinkedInSearch(term="bad", location="Spain"), LinkedInSearch(term="good", location="Spain")],
                         results=10, hours_old=48, scrape=scrape)
    assert [j.url for j in src.fetch(SINCE)] == ["https://www.linkedin.com/jobs/view/3"]
    assert src.errors == ["linkedin:bad"]


def test_rows_without_url_or_title_skipped():
    src = LinkedInSource([LinkedInSearch(term="x", location="Spain")], results=5, hours_old=48,
                         scrape=lambda **kw: frame([{"job_url": "", "title": "T", "company": "C"}, {"job_url": "https://l/1", "title": "", "company": "C"}]))
    assert src.fetch(SINCE) == []


def test_settings_and_build(tmp_path):
    (tmp_path / "config.yaml").write_text("sources: [linkedin]\nlinkedin_searches: [{term: AI engineer, location: 'Málaga, Spain'}]\n")
    s = load_settings(tmp_path / "config.yaml")
    assert (s.linkedin_results, s.linkedin_hours_old) == (25, 48)
    (src,) = build_sources(s.sources, None, s)
    assert isinstance(src, LinkedInSource) and src.searches[0].location == "Málaga, Spain"
    assert "linkedin" not in load_settings(tmp_path / "none.yaml").sources  # off unless you list it
