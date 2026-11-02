"""Common pieces for job sources."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from jobcopilot.models import Job

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) jobcopilot/0.1"


class SourceError(Exception):
    """A source could not be fetched or parsed."""


class Source(Protocol):
    name: str

    def fetch(self, since: datetime) -> list[Job]: ...


def http_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, transport=transport, follow_redirects=True)


def get_json(client: httpx.Client, url: str) -> Any:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SourceError(f"{url}: {e}") from e


def parse_dt(value: str | int | float | None) -> datetime | None:
    """Parse ISO strings or unix timestamps; naive values are taken as UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_recent(job: Job, since: datetime) -> bool:
    return job.posted_at is None or job.posted_at >= since
