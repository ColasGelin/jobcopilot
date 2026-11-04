"""Poll the dashboard and carry out its commands in a visible Chromium on the Mac."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Callable

from jobcopilot.agent.api import AgentAPI, DashboardUnreachable
from jobcopilot.agent.fill import fill_fields, reveal_form
from jobcopilot.apply.browser import read_page
from jobcopilot.apply.models import FormSnapshot

POLL_S = 3
RETRY_S = 30
BROWSER_DIR = Path.home() / ".jobcopilot" / "browser"


class Agent:
    def __init__(self, api: AgentAPI, open_page: Callable[[str], object], sleep=time.sleep, log=print):
        self.api, self.open_page, self.sleep, self.log = api, open_page, sleep, log
        self.pages: dict[int, object] = {}  # one window per application, kept open between commands
        self.downloads = Path(tempfile.mkdtemp(prefix="jobcopilot-cv-"))

    def _window(self, app_id: int):
        page = self.pages.get(app_id)
        return page if page is not None and not page.is_closed() else None

    def step(self) -> bool:
        """Handle one command if there is one. Returns whether there was."""
        cmd = self.api.next_command()
        if cmd is None:
            return False
        cmd_id, app_id = cmd["id"], cmd["app_id"]
        if not str(cmd["url"]).lower().startswith(("http://", "https://")):
            self.api.report(cmd_id, "failed", error="Refusing non-http URL")
            return True
        try:
            if cmd["action"] == "fill":
                self._fill(cmd_id, app_id)
            else:
                self._read(cmd_id, app_id, cmd["url"])
        except DashboardUnreachable:
            raise
        except Exception as e:
            self.api.report(cmd_id, "failed", error=str(e).splitlines()[0][:300] if str(e) else repr(e))
        return True

    def _fill(self, cmd_id: int, app_id: int) -> None:
        data = self.api.application(app_id)
        page = self._window(app_id)
        if page is None:
            page = self.pages[app_id] = self.open_page(data["job_url"])
            reveal_form(page, data["fields"])
        wants_cv = any(f.get("answer_kind") == "cv" for f in data["fields"])
        cv = self.api.download_cv(app_id, self.downloads) if wants_cv else None
        wants_cover = any(f.get("answer_kind") == "cover" for f in data["fields"])
        cover = self.api.download_cover(app_id, self.downloads) if wants_cover else None
        result = fill_fields(page, data["fields"], cv, cover)
        self.api.report(cmd_id, "done", filled=result.filled, unfilled=result.unfilled)
        self.log(f"Filled {len(result.filled)} fields ({len(result.unfilled)} left). Check the window and submit yourself.")

    def _read(self, cmd_id: int, app_id: int, url: str) -> None:
        page = self._window(app_id)
        if page is None:  # first time: open the window and let the candidate sign in / click Next
            self.pages[app_id] = self.open_page(url)
            self.api.report(cmd_id, "waiting")
            return
        snap = read_page(page, "", click_apply=False)  # the candidate drives this window
        if isinstance(snap, FormSnapshot):
            self.api.post_form(app_id, snap)
            self.api.report(cmd_id, "done")
        else:
            self.api.report(cmd_id, "waiting", error=snap.reason)

    def run_forever(self) -> None:
        down = False
        while True:
            try:
                handled = self.step()
                down = False
            except DashboardUnreachable:
                if not down:
                    self.log("Dashboard unreachable (is the SSH tunnel open?); retrying every 30 s.")
                down = True
                self.sleep(RETRY_S)
                continue
            except Exception as e:  # an HTTP 500, a broken report: never let the agent (and its windows) die
                self.log(f"Agent error, retrying in 30 s: {e!r}")
                self.sleep(RETRY_S)
                continue
            if not handled:
                self.sleep(POLL_S)


def run_agent(dashboard: str) -> None:
    import httpx
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        BROWSER_DIR.mkdir(parents=True, exist_ok=True)
        browser = {"context": p.chromium.launch_persistent_context(str(BROWSER_DIR), headless=False)}

        def open_page(url: str):
            try:
                page = browser["context"].new_page()
            except Exception:  # the candidate quit Chromium: start it again
                browser["context"] = p.chromium.launch_persistent_context(str(BROWSER_DIR), headless=False)
                page = browser["context"].new_page()
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            page.bring_to_front()
            return page

        print(f"jobcopilot agent: watching {dashboard} (Ctrl+C to stop)")
        with httpx.Client(timeout=30) as client:
            Agent(AgentAPI(dashboard, client), open_page).run_forever()
