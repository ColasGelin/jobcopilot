import builtins
import json
from pathlib import Path

import httpx

from jobcopilot.apply import reader
from jobcopilot.apply.models import FormSnapshot, NeedsYou
from jobcopilot.config import WatchlistEntry

FIXTURES = Path(__file__).parent / "fixtures"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_greenhouse_jobs_use_the_api(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=json.loads((FIXTURES / "greenhouse_questions.json").read_text()))

    monkeypatch.setattr(reader, "read_in_browser", lambda url: (_ for _ in ()).throw(AssertionError("no browser")))
    snap = reader.read_form("https://job-boards.greenhouse.io/acme/jobs/123", "Acme", [], client(handler))
    assert isinstance(snap, FormSnapshot) and seen == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/123?questions=true"]
    assert snap.url == "https://job-boards.greenhouse.io/acme/jobs/123"


def test_greenhouse_api_failure_falls_back_to_browser(monkeypatch):
    monkeypatch.setattr(reader, "read_in_browser", lambda url: NeedsYou(reason="from browser"))
    out = reader.read_form("https://job-boards.greenhouse.io/acme/jobs/123", "Acme", [], client(lambda r: httpx.Response(404)))
    assert out == NeedsYou(reason="from browser")


def test_other_jobs_use_the_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(reader, "read_in_browser", lambda url: calls.append(url) or NeedsYou(reason="x"))
    reader.read_form("https://jobs.ashbyhq.com/acme/1", "Acme", [WatchlistEntry(name="Acme", ats="ashby", slug="acme")],
                     client(lambda r: httpx.Response(500)))
    assert calls == ["https://jobs.ashbyhq.com/acme/1"]


def test_non_http_urls_refused():
    assert reader.read_form("javascript:alert(1)", "A", [], client(lambda r: httpx.Response(500))).reason.startswith("Not a web page")


def test_browser_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert reader.read_in_browser("https://x") == NeedsYou(reason="Browser not installed on the server")


def test_greenhouse_odd_payload_falls_back_to_browser(monkeypatch):
    monkeypatch.setattr(reader, "read_in_browser", lambda url: NeedsYou(reason="from browser"))
    out = reader.read_form("https://job-boards.greenhouse.io/acme/jobs/123", "Acme", [],
                           client(lambda r: httpx.Response(200, json={"questions": "oops"})))
    assert out == NeedsYou(reason="from browser")
