"""Public job feeds, each normalized to Job."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from jobcopilot.sources.arbeitnow import ArbeitnowSource
from jobcopilot.sources.base import Source, SourceError, http_client
from jobcopilot.sources.himalayas import HimalayasSource
from jobcopilot.sources.hn import HNSource
from jobcopilot.sources.jobicy import JobicySource
from jobcopilot.sources.remoteok import RemoteOKSource
from jobcopilot.sources.remotive import RemotiveSource
from jobcopilot.sources.watchlist import WatchlistSource
from jobcopilot.sources.workingnomads import WorkingNomadsSource
from jobcopilot.sources.wwr import WWRSource

if TYPE_CHECKING:
    from jobcopilot.config import Settings

SIMPLE = {cls.name: cls for cls in (RemotiveSource, RemoteOKSource, ArbeitnowSource, HNSource, JobicySource, WorkingNomadsSource, WWRSource)}
REGISTRY = SIMPLE  # kept for backwards compatibility


def build_sources(names: list[str], client: httpx.Client, settings: Settings | None = None) -> list[Source]:
    sources: list[Source] = []
    for n in names:
        if n == "himalayas":
            queries = settings.himalayas_queries if settings else ["python"]
            sources.append(HimalayasSource(client, queries, settings.himalayas_country if settings else "Spain"))
        elif n == "watchlist":
            sources.append(WatchlistSource(client, settings.watchlist if settings else []))
        elif n == "linkedin":
            from jobcopilot.config import Settings
            from jobcopilot.sources.linkedin import LinkedInSource

            s = settings or Settings(data_dir=".")
            sources.append(LinkedInSource(s.linkedin_searches, s.linkedin_results, s.linkedin_hours_old))
        else:
            sources.append(SIMPLE[n](client))
    return sources


__all__ = ["ArbeitnowSource", "HNSource", "HimalayasSource", "JobicySource", "RemoteOKSource", "RemotiveSource", "Source",
           "SourceError", "WWRSource", "WatchlistSource", "WorkingNomadsSource", "build_sources", "http_client"]
