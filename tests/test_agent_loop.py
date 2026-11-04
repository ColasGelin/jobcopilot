import pytest

from jobcopilot.agent import loop
from jobcopilot.agent.api import DashboardUnreachable
from jobcopilot.agent.fill import FillResult
from jobcopilot.apply.models import FormField, FormSnapshot, NeedsYou

SNAP = FormSnapshot(url="https://x", fields=[FormField(key="k", selector="#k", label="K", kind="text")])


class FakeAPI:
    def __init__(self, commands, fields=None):
        self.commands, self.reports, self.forms = list(commands), [], []
        self.fields = fields or [{"key": "k", "answer_kind": "open", "value": "v"}, {"key": "cv", "answer_kind": "cv", "value": "CV"}]

    def next_command(self):
        if not self.commands:
            return None
        c = self.commands.pop(0)
        if isinstance(c, Exception):
            raise c
        return c

    def application(self, app_id):
        return {"job_url": "https://jobs/1", "fields": self.fields}

    def download_cv(self, app_id, dest):
        return dest / "CV.pdf"

    def download_cover(self, app_id, dest):
        return dest / "Cover-Letter.pdf"

    def post_form(self, app_id, snap):
        self.forms.append((app_id, snap))

    def report(self, cmd_id, status, filled=(), unfilled=(), error=None):
        self.reports.append((cmd_id, status, list(filled), list(unfilled), error))


class FakePage:
    def __init__(self, url):
        self.url, self.closed = url, False

    def is_closed(self):
        return self.closed


@pytest.fixture
def opened():
    pages = []

    def open_page(url):
        page = FakePage(url)
        pages.append(page)
        return page

    return pages, open_page


def test_fill_command_fills_and_reports_done(monkeypatch, opened):
    pages, open_page = opened
    calls = []
    monkeypatch.setattr(loop, "fill_fields", lambda page, fields, cv, cover: calls.append((page, fields, cv, cover)) or FillResult(["k"], [{"key": "x", "label": "X", "why": "not found"}]))
    monkeypatch.setattr(loop, "reveal_form", lambda page, fields: None)
    api = FakeAPI([{"id": 1, "app_id": 7, "action": "fill", "url": "https://jobs/1"}])
    agent = loop.Agent(api, open_page)
    assert agent.step() is True
    assert [p.url for p in pages] == ["https://jobs/1"] and calls[0][2].name == "CV.pdf"
    assert api.reports == [(1, "done", ["k"], [{"key": "x", "label": "X", "why": "not found"}], None)]
    assert agent.step() is False


def test_read_command_waits_then_reads_on_retake(monkeypatch, opened):
    pages, open_page = opened
    results = [NeedsYou(reason="Sign-in or account creation required"), SNAP]
    monkeypatch.setattr(loop, "read_page", lambda page, url, click_apply=True: results.pop(0))
    cmd = {"id": 2, "app_id": 7, "action": "read", "url": "https://jobs/1"}
    api = FakeAPI([cmd, cmd, cmd])
    agent = loop.Agent(api, open_page)
    agent.step()
    assert api.reports[-1][:2] == (2, "waiting") and len(pages) == 1
    agent.step()  # still on the sign-in page
    assert api.reports[-1][1] == "waiting" and "Sign-in" in api.reports[-1][4]
    agent.step()
    assert api.forms == [(7, SNAP)] and api.reports[-1][1] == "done" and len(pages) == 1


def test_fill_reuses_the_window_of_a_read(monkeypatch, opened):
    pages, open_page = opened
    monkeypatch.setattr(loop, "read_page", lambda page, url, click_apply=True: SNAP)
    monkeypatch.setattr(loop, "fill_fields", lambda page, fields, cv, cover: FillResult(["k"], []))
    monkeypatch.setattr(loop, "reveal_form", lambda page, fields: pytest.fail("must not reload a page the candidate signed in on"))
    api = FakeAPI([{"id": 1, "app_id": 7, "action": "read", "url": "https://jobs/1"},
                   {"id": 1, "app_id": 7, "action": "read", "url": "https://jobs/1"},
                   {"id": 2, "app_id": 7, "action": "fill", "url": "https://jobs/1"}])
    agent = loop.Agent(api, open_page)
    agent.step(); agent.step(); agent.step()
    assert len(pages) == 1 and api.reports[-1][:2] == (2, "done")


def test_refuses_non_http_url(opened):
    pages, open_page = opened
    api = FakeAPI([{"id": 3, "app_id": 7, "action": "fill", "url": "javascript:alert(1)"}])
    loop.Agent(api, open_page).step()
    assert pages == [] and api.reports == [(3, "failed", [], [], "Refusing non-http URL")]


def test_errors_reported_as_failed(monkeypatch, opened):
    _, open_page = opened
    monkeypatch.setattr(loop, "fill_fields", lambda *a: (_ for _ in ()).throw(RuntimeError("page crashed")))
    monkeypatch.setattr(loop, "reveal_form", lambda page, fields: None)
    api = FakeAPI([{"id": 4, "app_id": 7, "action": "fill", "url": "https://jobs/1"}])
    loop.Agent(api, open_page).step()
    assert api.reports[0][1] == "failed" and "page crashed" in api.reports[0][4]


def test_unreachable_dashboard_backs_off(opened):
    _, open_page = opened
    sleeps, logs = [], []
    api = FakeAPI([DashboardUnreachable("down"), DashboardUnreachable("down"), None])

    class Stop(Exception):
        pass

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            raise Stop

    agent = loop.Agent(api, open_page, sleep=sleep, log=logs.append)
    with pytest.raises(Stop):
        agent.run_forever()
    assert sleeps == [30, 30, 3] and len([l for l in logs if "unreachable" in l]) == 1


def test_mac_read_never_clicks_apply(monkeypatch, opened):
    _, open_page = opened
    seen = []
    monkeypatch.setattr(loop, "read_page", lambda page, url, click_apply=True: seen.append(click_apply) or SNAP)
    cmd = {"id": 2, "app_id": 7, "action": "read", "url": "https://jobs/1"}
    agent = loop.Agent(FakeAPI([cmd, cmd]), open_page)
    agent.step(); agent.step()
    assert seen == [False]


def test_run_forever_survives_other_errors(opened):
    _, open_page = opened
    sleeps, logs = [], []
    api = FakeAPI([RuntimeError("HTTP 500"), None])

    class Stop(Exception):
        pass

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 2:
            raise Stop

    with pytest.raises(Stop):
        loop.Agent(api, open_page, sleep=sleep, log=logs.append).run_forever()
    assert sleeps == [30, 3] and any("HTTP 500" in l for l in logs)


def test_fill_downloads_cover_letter_only_when_asked(monkeypatch, opened):
    _, open_page = opened
    calls = []
    monkeypatch.setattr(loop, "fill_fields", lambda page, fields, cv, cover: calls.append((cv, cover)) or FillResult([], []))
    monkeypatch.setattr(loop, "reveal_form", lambda page, fields: None)
    api = FakeAPI([{"id": 1, "app_id": 7, "action": "fill", "url": "https://jobs/1"}])
    loop.Agent(api, open_page).step()
    assert calls[0][1] is None
    api = FakeAPI([{"id": 1, "app_id": 8, "action": "fill", "url": "https://jobs/1"}],
                  fields=[{"key": "cl", "answer_kind": "cover", "value": "Cover letter"}])
    loop.Agent(api, open_page).step()
    assert calls[1][0] is None and calls[1][1].name == "Cover-Letter.pdf"
