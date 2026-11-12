"""A job page the candidate saved from their browser (File > Save Page As…) and uploaded to the dashboard.

Used for pages the server can't open itself: LinkedIn Easy Apply, pages behind a sign-in. The file is shown in
headless Chromium with its JavaScript off and every network request blocked, so it can't run or call anything;
then its text and form fields are read like a live page."""

from __future__ import annotations

import html as html_lib
import re

from pydantic import BaseModel

from jobcopilot.apply.models import FormSnapshot, NeedsYou
from jobcopilot.apply.reader import NO_BROWSER

MAX_BYTES = 10_000_000

# Where the page came from: Chrome and Safari write a "saved from url" comment; most sites also name their own address.
_URL_PATTERNS = (
    re.compile(r"<!--\s*saved from url=\(\d+\)(\S+?)\s*-->", re.I),
    re.compile(r"<link\b[^>]*\brel=[\"']canonical[\"'][^>]*\bhref=[\"']([^\"']+)", re.I),
    re.compile(r"<link\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*\brel=[\"']canonical[\"']", re.I),
    re.compile(r"<meta\b[^>]*\bproperty=[\"']og:url[\"'][^>]*\bcontent=[\"']([^\"']+)", re.I),
    re.compile(r"<meta\b[^>]*\bcontent=[\"']([^\"']+)[\"'][^>]*\bproperty=[\"']og:url[\"']", re.I),
)


class SavedPage(BaseModel):
    url: str  # "" when the file doesn't say where it was saved from
    title: str
    text: str
    form: FormSnapshot | NeedsYou


def page_url(html: str) -> str:
    """The web address the page was saved from, or "" if the file doesn't say."""
    for pattern in _URL_PATTERNS:
        if (m := pattern.search(html)) and (url := html_lib.unescape(m.group(1)).strip()).lower().startswith(("http://", "https://")):
            return url
    return ""


def read_saved_page(html: str) -> SavedPage | NeedsYou:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return NeedsYou(reason=NO_BROWSER)
    from jobcopilot.apply.browser import _extract, classify

    url = page_url(html)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            return NeedsYou(reason=f"{NO_BROWSER}: {str(e).splitlines()[0][:150]}")
        try:
            context = browser.new_context(java_script_enabled=False)
            context.route("**/*", lambda route: route.abort())  # nothing leaves the machine: no images, scripts or trackers
            page = context.new_page()
            page.set_content(html, wait_until="domcontentloaded")
            form = classify(_extract(page), url)
            return SavedPage(url=url, title=page.title(), text=page.inner_text("body"), form=form)
        except Exception as e:
            return NeedsYou(reason=f"The saved page could not be read: {str(e).splitlines()[0][:200]}")
        finally:
            browser.close()
