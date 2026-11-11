"""Where a hiring platform keeps the application form of a job page, for pages whose own button won't reveal it.

Each rule was checked on live postings. Teamtailor careers sites often run on the company's own domain, so they are
recognised by the page's HTML instead of its address."""

from __future__ import annotations

import re

# (platform, URL pattern of a job page with the job's base address as group 1, suffix of its form page)
_BY_URL = (
    ("ashby", re.compile(r"^(https://jobs\.ashbyhq\.com/[^/?#]+/[0-9a-f-]{36})(?:[/?#]|$)", re.I), "/application"),
    ("lever", re.compile(r"^(https://jobs(?:\.eu)?\.lever\.co/[^/?#]+/[0-9a-f-]{36})(?:[/?#]|$)", re.I), "/apply"),
    ("workable", re.compile(r"^(https://apply\.workable\.com/[^/?#]+/j/[0-9A-F]+)(?:[/?#]|$)", re.I), "/apply/"),
    ("recruitee", re.compile(r"^(https://[^/]+\.recruitee\.com/o/[^/?#]+)(?:[/?#]|$)", re.I), "/c/new"),
    ("teamtailor", re.compile(r"^(https://[^/]+\.teamtailor\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?jobs/\d+[^/?#]*)(?:[/?#]|$)", re.I),
     "/applications/new"),
)
_TEAMTAILOR_JOB = re.compile(r"^(https?://[^/]+/(?:[a-z]{2}(?:-[a-z]{2})?/)?jobs/\d+[^/?#]*)(?:[/?#]|$)", re.I)


def form_url(url: str, html: str = "") -> str | None:
    """The platform's form page for the job page at `url` (its HTML recognises custom domains), or None."""
    target = None
    for _, pattern, suffix in _BY_URL:
        if m := pattern.match(url):
            target = m.group(1) + suffix
            break
    else:
        if "teamtailor" in html.lower() and (m := _TEAMTAILOR_JOB.match(url)):
            target = m.group(1) + "/applications/new"
    on_it = target and re.split(r"[?#]", url)[0].rstrip("/").startswith(target.rstrip("/"))
    return None if on_it else target
