from datetime import datetime
from urllib.parse import urlencode

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, is_recent, parse_dt
from jobcopilot.text import html_to_text

SEARCH_URL = "https://himalayas.app/jobs/api/search?"


class HimalayasSource:
    """Remote jobs searched by keyword, restricted to those open to `country`."""

    name = "himalayas"

    def __init__(self, client: httpx.Client, queries: list[str], country: str):
        self.client, self.queries, self.country = client, queries, country

    def _location(self, restrictions: list[str]) -> str:
        if not restrictions:
            return ""  # worldwide
        return self.country if self.country in restrictions else ", ".join(restrictions)

    def fetch(self, since: datetime) -> list[Job]:
        jobs: dict[str, Job] = {}
        for query in self.queries:
            data = get_json(self.client, SEARCH_URL + urlencode({"q": query, "country": self.country}))
            try:
                for item in data["jobs"]:
                    job = Job(
                        url=item["applicationLink"],
                        source=self.name,
                        company=item["companyName"],
                        title=html_to_text(item["title"]),
                        location=self._location(item.get("locationRestrictions") or []),
                        remote=True,
                        salary_text=(
                            f"{item['minSalary']} - {item['maxSalary']} {item.get('currency') or ''}".strip()
                            if item.get("minSalary") and item.get("maxSalary") else None
                        ),
                        description=html_to_text(item.get("description") or item.get("excerpt") or ""),
                        posted_at=parse_dt(int(item["pubDate"])) if item.get("pubDate") else None,
                    )
                    jobs.setdefault(job.url, job)
            except (KeyError, TypeError, ValueError) as e:
                raise SourceError(f"himalayas: unexpected format: {e}") from e
        return [j for j in jobs.values() if is_recent(j, since)]
