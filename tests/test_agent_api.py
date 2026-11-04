import httpx
import pytest

from jobcopilot.agent.api import AgentAPI, DashboardUnreachable
from jobcopilot.apply.models import FormField, FormSnapshot


def api(handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        assert request.headers["X-Jobcopilot-Agent"] == "1"
        return handler(request)

    return AgentAPI("http://localhost:8077", httpx.Client(transport=httpx.MockTransport(wrapped))), seen


def test_next_command_and_204():
    a, seen = api(lambda r: httpx.Response(204))
    assert a.next_command() is None and str(seen[0].url) == "http://localhost:8077/api/agent/next"
    a, _ = api(lambda r: httpx.Response(200, json={"id": 1, "app_id": 2, "action": "fill", "url": "https://x"}))
    assert a.next_command()["action"] == "fill"


def test_report_and_post_form():
    a, seen = api(lambda r: httpx.Response(200, json={"ok": True, "status": "ready"}))
    a.report(5, "done", filled=["a"], unfilled=[{"key": "b", "label": "B", "why": "x"}])
    a.post_form(3, FormSnapshot(url="https://x", fields=[FormField(key="k", selector="#k", label="K", kind="text")]))
    assert str(seen[0].url).endswith("/api/agent/commands/5/result") and b'"done"' in seen[0].content
    assert str(seen[1].url).endswith("/api/applications/3/form") and b'"#k"' in seen[1].content


def test_download_cv(tmp_path):
    a, _ = api(lambda r: httpx.Response(200, content=b"%PDF", headers={"content-disposition": 'attachment; filename="Alex-Martin-CV.pdf"'}))
    path = a.download_cv(3, tmp_path)
    assert path == tmp_path / "Alex-Martin-CV.pdf" and path.read_bytes() == b"%PDF"
    a, _ = api(lambda r: httpx.Response(404))
    assert a.download_cv(3, tmp_path) is None


def test_unreachable_and_http_errors():
    def down(request):
        raise httpx.ConnectError("refused")

    a, _ = api(down)
    with pytest.raises(DashboardUnreachable):
        a.next_command()
    a, _ = api(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        a.application(1)


def test_download_cover(tmp_path):
    a, seen = api(lambda r: httpx.Response(200, content=b"%PDF", headers={"content-disposition": 'attachment; filename="Alex-Martin-Cover-Letter.pdf"'}))
    assert a.download_cover(3, tmp_path) == tmp_path / "Alex-Martin-Cover-Letter.pdf"
    assert str(seen[0].url).endswith("/api/applications/3/cover.pdf")
