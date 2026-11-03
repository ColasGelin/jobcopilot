"""Server side: read a job's application form, via Greenhouse's API when possible, else headless Chromium."""

from __future__ import annotations

import time

import httpx

from jobcopilot.apply.greenhouse import QUESTIONS_URL, greenhouse_ref, snapshot_from_greenhouse
from jobcopilot.apply.models import FormSnapshot, NeedsYou
from jobcopilot.config import WatchlistEntry

JOB_TIMEOUT_S = 60
NO_BROWSER = "Browser not installed on the server"


def read_in_browser(url: str) -> FormSnapshot | NeedsYou:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return NeedsYou(reason=NO_BROWSER)
    from jobcopilot.apply.browser import read_page

    started = time.monotonic()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(JOB_TIMEOUT_S * 1000)
            result = read_page(page, url)
            if time.monotonic() - started > JOB_TIMEOUT_S:
                return NeedsYou(reason=f"Page took more than {JOB_TIMEOUT_S} s to read")
            return result
        except Exception as e:
            return NeedsYou(reason=f"Page failed to load: {str(e).splitlines()[0][:200]}")
        finally:
            browser.close()


def read_form(url: str, company: str, watchlist: list[WatchlistEntry], http: httpx.Client) -> FormSnapshot | NeedsYou:
    if not url.lower().startswith(("http://", "https://")):
        return NeedsYou(reason=f"Not a web page: {url[:100]}")
    ref = greenhouse_ref(url, company, watchlist)
    if ref:
        try:
            response = http.get(QUESTIONS_URL.format(slug=ref[0], id=ref[1]), timeout=30)
            response.raise_for_status()
            return snapshot_from_greenhouse(response.json(), url)
        except (httpx.HTTPError, ValueError):
            pass  # job closed or API changed: try the page itself
    return read_in_browser(url)
