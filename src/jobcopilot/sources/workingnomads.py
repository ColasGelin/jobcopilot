from datetime import datetime

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, is_recent, parse_dt
from jobcopilot.text import html_to_text

URL = "https://www.workingnomads.com/api/exposed_jobs/"


class WorkingNomadsSource:
    name = "workingnomads"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: datetime) -> list[Job]:
        data = get_json(self.client, URL)
        if not isinstance(data, list):
            raise SourceError("workingnomads: expected a list")
        try:
            jobs = [
                Job(
                    url=item["url"],
                    source=self.name,
                    company=item["company_name"],
                    title=html_to_text(item["title"]),
                    location=item.get("location") or "",
                    remote=True,
                    description=html_to_text(item.get("description") or ""),
                    posted_at=parse_dt(item.get("pub_date")),
                )
                for item in data
                if item.get("category_name") == "Development"
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError(f"workingnomads: unexpected format: {e}") from e
        return [j for j in jobs if is_recent(j, since)]
