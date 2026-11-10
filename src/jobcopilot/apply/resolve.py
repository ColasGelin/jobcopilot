"""Find a job's own application page when the page we have shows no form: the same role on the company's public
Greenhouse, Ashby, Lever, Recruitee, Workable or BambooHR board (LinkedIn Easy Apply and job-board pages usually hide the company's link)."""

from __future__ import annotations

import re

import httpx

from jobcopilot.apply.browser import NO_FORM, SIGN_IN
from jobcopilot.models import Job
from jobcopilot.text import normalize

EASY_APPLY = ("LinkedIn Easy Apply: apply on LinkedIn yourself (it needs your login), or paste the company's own "
              "application link under Links")

# Public job listings by provider: (listing URL, (json, slug) -> [(title, posting URL)]).
_LISTINGS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                   lambda d, s: [(j["title"], j["absolute_url"]) for j in d["jobs"]]),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", lambda d, s: [(j["title"], j["jobUrl"]) for j in d["jobs"]]),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", lambda d, s: [(j["text"], j["hostedUrl"]) for j in d]),
    "recruitee": ("https://{slug}.recruitee.com/api/offers/", lambda d, s: [(j["title"], j["careers_url"]) for j in d["offers"]]),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{slug}", lambda d, s: [(j["title"], j["url"]) for j in d["jobs"]]),
    "bamboohr": ("https://{slug}.bamboohr.com/careers/list",
                 lambda d, s: [(j["jobOpeningName"], f"https://{s}.bamboohr.com/careers/{j['id']}") for j in d["result"]]),
}
_SUFFIXES = {"sl", "sa", "slu", "inc", "ltd", "llc", "gmbh", "ag", "bv", "sas", "corp", "co", "plc", "espana", "spain", "group"}


def slug_candidates(company: str) -> list[str]:
    """Board ids a company is likely to use: its name without legal suffixes, joined and hyphenated."""
    words = re.findall(r"[a-z0-9]+", normalize(company).replace(".", ""))
    while words and words[-1] in _SUFFIXES:
        words.pop()
    if not words:
        return []
    joined, hyphenated = "".join(words), "-".join(words)
    return [joined] if joined == hyphenated else [joined, hyphenated]


def _core(title: str) -> set[str]:
    title = re.split(r"\s[-|–]\s", re.sub(r"\(.*?\)", " ", normalize(title)))[0]
    return set(re.findall(r"[a-z0-9+#]+", title.replace("&", " and ")))


def same_role(a: str, b: str) -> bool:
    return bool(_core(a)) and _core(a) == _core(b)


def find_on_ats(job: Job, http: httpx.Client) -> str | None:
    """The URL of the same role on the company's own board, or None."""
    for slug in slug_candidates(job.company):
        for url, parse in _LISTINGS.values():
            try:
                response = http.get(url.format(slug=slug), timeout=15)
                if response.status_code != 200:
                    continue
                postings = parse(response.json(), slug)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                continue
            for title, posting_url in postings:
                if same_role(title, job.title):
                    return posting_url
    return None


def explain(url: str, reason: str) -> str:
    """A clearer reason for pages we know: LinkedIn shows no form when the job uses Easy Apply."""
    if "linkedin.com/" in url and reason in (NO_FORM, SIGN_IN):
        return EASY_APPLY
    return reason
