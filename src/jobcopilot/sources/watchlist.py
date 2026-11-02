"""Open jobs at chosen companies, from their public Greenhouse / Ashby / Lever job boards."""

from datetime import datetime

import httpx

from jobcopilot.config import WatchlistEntry
from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, parse_dt
from jobcopilot.text import html_to_text, normalize

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
}


def _greenhouse(data: dict, company: str) -> list[Job]:
    jobs = []
    for item in data["jobs"]:
        location = (item.get("location") or {}).get("name") or ""
        jobs.append(Job(
            url=item["absolute_url"], source="watchlist", company=company, title=item["title"], location=location,
            remote="remote" in normalize(f"{location} {item['title']}"),
            description=html_to_text(item.get("content") or ""),
            posted_at=parse_dt(item.get("first_published") or item.get("updated_at")),
        ))
    return jobs


def _ashby(data: dict, company: str) -> list[Job]:
    jobs = []
    for item in data["jobs"]:
        if item.get("isListed") is False:
            continue
        places = [item.get("location") or ""] + [s.get("location") or "" for s in item.get("secondaryLocations") or []]
        comp = (item.get("compensation") or {}).get("compensationTierSummary")
        jobs.append(Job(
            url=item["jobUrl"], source="watchlist", company=company, title=item["title"],
            location=", ".join(p for p in places if p),
            remote=bool(item.get("isRemote")) or item.get("workplaceType") == "Remote",
            salary_text=comp or None,
            description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml") or ""),
            posted_at=parse_dt(item.get("publishedAt")),
        ))
    return jobs


def _lever(data: list, company: str) -> list[Job]:
    jobs = []
    for item in data:
        cats = item.get("categories") or {}
        places = cats.get("allLocations") or [cats.get("location") or ""]
        jobs.append(Job(
            url=item["hostedUrl"], source="watchlist", company=company, title=item["text"],
            location=", ".join(p for p in places if p),
            remote=item.get("workplaceType") == "remote" or "remote" in normalize(cats.get("commitment") or ""),
            description=item.get("descriptionPlain") or html_to_text(item.get("description") or ""),
            posted_at=parse_dt(item["createdAt"] / 1000) if item.get("createdAt") else None,
        ))
    return jobs


PARSERS = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever}


class WatchlistSource:
    """`since` is not applied: these boards list currently open jobs, and dedupe prevents repeats."""

    name = "watchlist"

    def __init__(self, client: httpx.Client, entries: list[WatchlistEntry]):
        self.client, self.entries = client, entries
        self.errors: list[str] = []

    def fetch(self, since: datetime) -> list[Job]:
        self.errors = []
        jobs: list[Job] = []
        for entry in self.entries:
            try:
                data = get_json(self.client, URLS[entry.ats].format(slug=entry.slug))
                jobs.extend(PARSERS[entry.ats](data, entry.name))
            except (SourceError, KeyError, TypeError, ValueError):
                self.errors.append(f"watchlist:{entry.name}")
        return jobs
