import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from jobcopilot.sources import ArbeitnowSource, RemoteOKSource, RemotiveSource, SourceError, http_client

FIX = Path(__file__).parent / "fixtures"
SINCE = datetime(2026, 10, 30, tzinfo=timezone.utc)
ALL = datetime(2020, 1, 1, tzinfo=timezone.utc)


def client_serving(name: str) -> httpx.Client:
    body = (FIX / f"{name}.json").read_bytes()
    return http_client(httpx.MockTransport(lambda req: httpx.Response(200, content=body, headers={"content-type": "application/json"})))


def client_returning(status: int, body: bytes, ctype: str) -> httpx.Client:
    return http_client(httpx.MockTransport(lambda req: httpx.Response(status, content=body, headers={"content-type": ctype})))


def test_remotive_parses_jobs():
    jobs = RemotiveSource(client_serving("remotive")).fetch(ALL)
    assert len(jobs) == 3
    assert all(j.source == "remotive" and j.remote for j in jobs)
    assert all("<" not in j.description for j in jobs)


def test_remoteok_skips_legal_notice():
    jobs = RemoteOKSource(client_serving("remoteok")).fetch(ALL)
    assert len(jobs) == 3
    assert all(j.company for j in jobs)


def test_arbeitnow_parses_unix_timestamp_and_entities():
    jobs = ArbeitnowSource(client_serving("arbeitnow")).fetch(ALL)
    assert jobs[0].posted_at == datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
    assert jobs[0].description.startswith("Build AI agents in Málaga & remote.")


@pytest.mark.parametrize("cls,name", [(RemotiveSource, "remotive"), (RemoteOKSource, "remoteok"), (ArbeitnowSource, "arbeitnow")])
def test_sources_drop_jobs_older_than_since(cls, name):
    jobs = cls(client_serving(name)).fetch(SINCE)
    assert len(jobs) == 2


@pytest.mark.parametrize("cls", [RemotiveSource, RemoteOKSource, ArbeitnowSource])
def test_source_error_on_html_body(cls):
    with pytest.raises(SourceError):
        cls(client_returning(200, b"<html>blocked</html>", "text/html")).fetch(SINCE)


@pytest.mark.parametrize("cls", [RemotiveSource, RemoteOKSource, ArbeitnowSource])
def test_source_error_on_500(cls):
    with pytest.raises(SourceError):
        cls(client_returning(500, b"oops", "text/plain")).fetch(SINCE)
