from datetime import datetime

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, is_recent, parse_dt
from jobcopilot.text import html_to_text

URL = "https://jobicy.com/api/v2/remote-jobs?count=50&geo=europe&industry=dev"


def _salary(item: dict) -> str | None:
    lo, hi, cur = item.get("salaryMin"), item.get("salaryMax"), item.get("salaryCurrency") or ""
    if not lo and not hi:
        return None
    parts = [f"{int(float(v)):,}" for v in (lo, hi) if v]
    return f"{' - '.join(parts)} {cur}".strip()


class JobicySource:
    name = "jobicy"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: datetime) -> list[Job]:
        data = get_json(self.client, URL)
        try:
            jobs = [
                Job(
                    url=item["url"],
                    source=self.name,
                    company=item["companyName"],
                    title=html_to_text(item["jobTitle"]),
                    location=", ".join(p.strip() for p in (item.get("jobGeo") or "").split(",") if p.strip()),
                    remote=True,
                    salary_text=_salary(item),
                    description=html_to_text(item.get("jobDescription") or item.get("jobExcerpt") or ""),
                    posted_at=parse_dt(item.get("pubDate")),
                )
                for item in data["jobs"]
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError(f"jobicy: unexpected format: {e}") from e
        return [j for j in jobs if is_recent(j, since)]
