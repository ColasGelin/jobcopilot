"""LinkedIn job search through JobSpy's guest endpoint: public listings, no login and no account, paced by JobSpy
(3-7 s between requests). It goes against LinkedIn's terms, so it is off unless `linkedin` is listed in `sources`;
the realistic risk is a temporary IP block, never the candidate's LinkedIn account."""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Callable

from jobcopilot.config import BoardSearch
from jobcopilot.models import Job
from jobcopilot.sources.base import parse_dt


def _value(row, key):
    v = row.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v


def _salary(row) -> str | None:
    lo, hi = _value(row, "min_amount"), _value(row, "max_amount")
    if lo is None and hi is None:
        return None
    amounts = " - ".join(f"{int(v):,}" for v in (lo, hi) if v is not None)
    interval = _value(row, "interval")
    return f"{amounts} {_value(row, 'currency') or ''}".strip() + (f" / {interval}" if interval else "")


class LinkedInSource:
    name = "linkedin"

    def __init__(self, searches: list[BoardSearch], results: int, hours_old: int, scrape: Callable | None = None,
                 pause_s: float = 0, wait: Callable[[float], None] = time.sleep):
        self.searches, self.results, self.hours_old = searches, results, hours_old
        self.scrape = scrape
        self.pause_s, self.wait = pause_s, wait
        self.errors: list[str] = []

    def options(self) -> dict:
        """Extra scrape_jobs arguments of this board."""
        return {}

    def _scrape(self, **kw):
        if self.scrape is None:
            from jobspy import scrape_jobs  # imported lazily: pandas is slow to load

            self.scrape = scrape_jobs
        return self.scrape(**kw)

    def fetch(self, since: datetime) -> list[Job]:
        """`since` is applied by the board itself through `hours_old`."""
        self.errors = []
        jobs: list[Job] = []
        for i, search in enumerate(self.searches):
            if i and self.pause_s:
                self.wait(self.pause_s)
            remote = {"is_remote": True} if search.remote else {}
            try:
                frame = self._scrape(site_name=[self.name], search_term=search.term, location=search.location,
                                     results_wanted=self.results, hours_old=self.hours_old, fetch_description=True, verbose=0,
                                     **remote, **self.options())
            except Exception:  # blocked (429) or the board changed: the other searches still run
                self.errors.append(f"{self.name}:{search.term}")
                continue
            for row in frame.to_dict("records"):
                url = _value(row, "job_url_direct") or _value(row, "job_url")  # the company's own form when LinkedIn shows it
                title, company = _value(row, "title"), _value(row, "company")
                if not url or not title:
                    continue
                location = str(_value(row, "location") or "")
                jobs.append(Job(
                    url=str(url), source=self.name, company=str(company or ""), title=str(title), location=location,
                    remote=search.remote or bool(_value(row, "is_remote")) or "remote" in location.lower(),
                    salary_text=_salary(row), description=str(_value(row, "description") or ""),
                    posted_at=parse_dt(str(_value(row, "date_posted"))) if _value(row, "date_posted") else None,
                ))
        return jobs
