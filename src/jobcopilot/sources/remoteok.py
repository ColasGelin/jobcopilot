from datetime import datetime

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, is_recent, parse_dt
from jobcopilot.text import html_to_text

URL = "https://remoteok.com/api?tag=dev"


def _salary(item: dict) -> str | None:
    lo, hi = item.get("salary_min") or 0, item.get("salary_max") or 0
    if not lo and not hi:
        return None
    return f"${lo:,} - ${hi:,}" if lo and hi else f"${lo or hi:,}"


class RemoteOKSource:
    name = "remoteok"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: datetime) -> list[Job]:
        data = get_json(self.client, URL)
        try:
            items = [i for i in data if isinstance(i, dict) and i.get("position")]  # element 0 is a legal notice
            jobs = [
                Job(
                    url=item["url"],
                    source=self.name,
                    company=item["company"],
                    title=item["position"],
                    location=item.get("location") or "",
                    remote=True,
                    salary_text=_salary(item),
                    description=html_to_text(item.get("description", "")),
                    posted_at=parse_dt(item.get("date")),
                )
                for item in items
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError(f"remoteok: unexpected format: {e}") from e
        return [j for j in jobs if is_recent(j, since)]
