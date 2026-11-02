from datetime import datetime

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, is_recent, parse_dt
from jobcopilot.text import html_to_text

URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowSource:
    name = "arbeitnow"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: datetime) -> list[Job]:
        data = get_json(self.client, URL)
        try:
            jobs = [
                Job(
                    url=item["url"],
                    source=self.name,
                    company=item["company_name"],
                    title=item["title"],
                    location=item.get("location") or "",
                    remote=bool(item.get("remote")),
                    description=html_to_text(item.get("description", "")),
                    posted_at=parse_dt(item.get("created_at")),
                )
                for item in data["data"]
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError(f"arbeitnow: unexpected format: {e}") from e
        return [j for j in jobs if is_recent(j, since)]
