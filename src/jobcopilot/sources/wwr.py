"""We Work Remotely programming jobs (RSS)."""

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, is_recent
from jobcopilot.text import html_to_text

URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


class WWRSource:
    name = "wwr"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: datetime) -> list[Job]:
        try:
            resp = self.client.get(URL)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (httpx.HTTPError, ET.ParseError) as e:
            raise SourceError(f"wwr: {e}") from e
        jobs = []
        for item in root.findall("channel/item"):
            raw_title = item.findtext("title") or ""
            company, _, title = raw_title.partition(": ")
            if not title:
                company, title = "Unknown", raw_title
            try:
                posted = parsedate_to_datetime(item.findtext("pubDate")) if item.findtext("pubDate") else None
            except (TypeError, ValueError):
                posted = None
            jobs.append(
                Job(
                    url=item.findtext("link") or item.findtext("guid") or "",
                    source=self.name,
                    company=company.strip(),
                    title=title.strip(),
                    location=(item.findtext("region") or "").strip(),
                    remote=True,
                    description=html_to_text(item.findtext("description") or ""),
                    posted_at=posted,
                )
            )
        return [j for j in jobs if j.url and is_recent(j, since)]
