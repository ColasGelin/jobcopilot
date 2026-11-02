"""Hacker News monthly "Ask HN: Who is hiring?" thread, via the Algolia API."""

import re
from datetime import datetime

import httpx

from jobcopilot.models import Job
from jobcopilot.sources.base import SourceError, get_json, parse_dt
from jobcopilot.text import html_to_text, normalize

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&query=who%20is%20hiring&hitsPerPage=5"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"
_SEP = re.compile(r"\s*\|\s*|\s+[—–]\s+")


def _header(text_html: str) -> str:
    return html_to_text(re.split(r"<p>", text_html, maxsplit=1)[0])


class HNSource:
    name = "hn"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: datetime) -> list[Job]:
        # `since` is not applied: the thread is monthly and most posts land in its first days;
        # the database dedupe stops already-seen posts from being re-scored.
        hits = get_json(self.client, SEARCH_URL).get("hits", [])
        thread = next((h for h in hits if h.get("title", "").startswith("Ask HN: Who is hiring?")), None)
        if thread is None:
            raise SourceError("hn: no 'Who is hiring?' thread found")
        item = get_json(self.client, ITEM_URL.format(id=thread["objectID"]))
        jobs = []
        for child in item.get("children", []):
            text = child.get("text")
            if not text:
                continue
            parts = [p for p in _SEP.split(_header(text)) if p]
            if len(parts) >= 2:
                company, title, location = parts[0], parts[1], " | ".join(parts[2:])
            else:
                company, title, location = " ".join(parts[0].split()[:5]) if parts else "Unknown", "See posting", ""
            description = html_to_text(text)
            jobs.append(
                Job(
                    url=f"https://news.ycombinator.com/item?id={child['id']}",
                    source=self.name,
                    company=company,
                    title=title,
                    location=location,
                    remote="remote" in normalize(description),
                    description=description,
                    posted_at=parse_dt(child.get("created_at")),
                )
            )
        return jobs
