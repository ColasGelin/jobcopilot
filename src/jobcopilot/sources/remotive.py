from datetime import datetime

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, is_recent, parse_dt
from jobcopilot.text import html_to_text

URL = "https://remotive.com/api/remote-jobs?category=software-dev"


class RemotiveSource:
    name = "remotive"

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
                    location=item.get("candidate_required_location") or "",
                    remote=True,
                    salary_text=item.get("salary") or None,
                    description=html_to_text(item.get("description", "")),
                    posted_at=parse_dt(item.get("publication_date")),
                )
                for item in data["jobs"]
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError(f"remotive: unexpected format: {e}") from e
        return [j for j in jobs if is_recent(j, since)]
