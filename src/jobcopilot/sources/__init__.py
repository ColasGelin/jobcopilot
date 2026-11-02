"""Public job feeds, each normalized to Job."""

import httpx

from jobcopilot.sources.arbeitnow import ArbeitnowSource
from jobcopilot.sources.hn import HNSource
from jobcopilot.sources.base import Source, SourceError, http_client
from jobcopilot.sources.remoteok import RemoteOKSource
from jobcopilot.sources.remotive import RemotiveSource

REGISTRY = {cls.name: cls for cls in (RemotiveSource, RemoteOKSource, ArbeitnowSource, HNSource)}


def build_sources(names: list[str], client: httpx.Client) -> list[Source]:
    return [REGISTRY[n](client) for n in names]


__all__ = ["ArbeitnowSource", "HNSource", "RemoteOKSource", "RemotiveSource", "Source", "SourceError", "build_sources", "http_client"]
