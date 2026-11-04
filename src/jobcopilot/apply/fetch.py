"""One job from a link the candidate pastes: the ATS APIs when the link is Greenhouse/Ashby/Lever, else the page.

On any other page, Claude only names the company, title, location and salary; the description is the page's
own text, so nothing about the job is invented."""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from jobcopilot.apply.greenhouse import greenhouse_ref
from jobcopilot.apply.models import NeedsYou
from jobcopilot.config import WatchlistEntry
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Job
from jobcopilot.sources.watchlist import _ashby, _greenhouse, _lever

DESCRIPTION_LIMIT = 8000
MIN_TEXT = 200  # fewer characters than this: a login wall or an empty page
PageText = Callable[[str], "tuple[str, str] | NeedsYou"]

_LEVER = re.compile(r"^https?://jobs\.(?:eu\.)?lever\.co/([^/]+)/([0-9a-f-]{20,})", re.I)
_ASHBY = re.compile(r"^https?://jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{20,})", re.I)

SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": "string"}, "title": {"type": "string"}, "location": {"type": "string"},
        "remote": {"type": "boolean"}, "salary_text": {"type": "string"},
    },
    "required": ["company", "title"],
}
PROMPT = """From this job posting page, give the hiring company's name, the job title, the location as written,
whether the job is remote, and the salary as written (empty if not stated). Copy them from the page; do not guess.

Page title: {title}
Page text:
{text}"""


class _Names(BaseModel):
    company: str
    title: str
    location: str = ""
    remote: bool = False
    salary_text: str = ""


def _company(slug: str, ats: str, watchlist: list[WatchlistEntry]) -> str:
    for entry in watchlist:
        if entry.ats == ats and entry.slug.lower() == slug.lower():
            return entry.name
    return slug.replace("-", " ").title()


def _from_ats(url: str, watchlist: list[WatchlistEntry], http: httpx.Client) -> Job | None:
    try:
        if m := _LEVER.match(url):
            data = http.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}?mode=json", timeout=20)
            data.raise_for_status()
            jobs = _lever([data.json()], _company(m.group(1), "lever", watchlist))
        elif m := _ASHBY.match(url):
            data = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}?includeCompensation=true", timeout=20)
            data.raise_for_status()
            jobs = [j for j in _ashby(data.json(), _company(m.group(1), "ashby", watchlist)) if m.group(2) in j.url]
        elif ref := greenhouse_ref(url, "", watchlist) or None:
            data = http.get(f"https://boards-api.greenhouse.io/v1/boards/{ref[0]}/jobs/{ref[1]}?content=true", timeout=20)
            data.raise_for_status()
            payload = data.json()
            jobs = _greenhouse({"jobs": [payload]}, payload.get("company_name") or _company(ref[0], "greenhouse", watchlist))
        else:
            return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None  # closed job or changed API: read the page instead
    return jobs[0].model_copy(update={"url": url, "source": "manual"}) if jobs else None


def fetch_job(url: str, watchlist: list[WatchlistEntry], http: httpx.Client, llm: LLMClient, model: str,
              page_text: PageText) -> Job | NeedsYou:
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return NeedsYou(reason=f"Not a web page: {url[:100]}")
    job = _from_ats(url, watchlist, http)
    if job is not None:
        return job
    page = page_text(url)
    if isinstance(page, NeedsYou):
        return page
    title, text = page
    if len(text.strip()) < MIN_TEXT:
        return NeedsYou(reason="The page has almost no text (a sign-in page or a closed offer?)")
    try:
        names = _Names.model_validate(llm.complete_json(PROMPT.replace("{title}", title).replace("{text}", text[:DESCRIPTION_LIMIT]), SCHEMA, model))
    except LLMUnavailable:
        raise
    except (LLMError, ValueError):
        names = _Names(company=urlparse(url).hostname or "", title=title.strip()[:150] or "Job")
    return Job(url=url, source="manual", company=names.company, title=names.title, location=names.location,
               remote=names.remote, salary_text=names.salary_text or None, description=text[:DESCRIPTION_LIMIT])
