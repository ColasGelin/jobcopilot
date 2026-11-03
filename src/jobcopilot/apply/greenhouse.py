"""Application questions from Greenhouse's public job board API (no browser needed)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from jobcopilot.apply.models import FormField, FormSnapshot
from jobcopilot.config import WatchlistEntry
from jobcopilot.text import html_to_text

QUESTIONS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}?questions=true"
PAGE_TEXT_LIMIT = 20_000

_BOARD = re.compile(r"^(?:job-)?boards(?:\.eu)?\.greenhouse\.io$")
_TYPES = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "checkbox",
}


def greenhouse_ref(url: str, company: str, watchlist: list[WatchlistEntry]) -> tuple[str, str] | None:
    """(board slug, job id) when the job is on Greenhouse, else None."""
    parsed = urlparse(url)
    if _BOARD.match(parsed.hostname or ""):
        m = re.match(r"^/([^/]+)/jobs/(\d+)", parsed.path)
        return (m.group(1), m.group(2)) if m else None
    gh_jid = parse_qs(parsed.query).get("gh_jid")
    if gh_jid:
        for entry in watchlist:
            if entry.ats == "greenhouse" and entry.name == company:
                return entry.slug, gh_jid[0]
    return None


def _fields(question: dict, eeo: bool = False) -> list[FormField]:
    fields = [f for f in question.get("fields") or [] if f.get("type") in _TYPES]
    if any(f["type"] == "input_file" for f in fields):  # "upload, or paste the text": keep the upload
        fields = [f for f in fields if f["type"] == "input_file"]
    return [
        FormField(
            key=f["name"], selector=f"#{f['name']}", label=(question.get("label") or "").strip(),
            description=html_to_text(question.get("description") or ""), kind=_TYPES[f["type"]],
            options=[str(v.get("label", "")) for v in f.get("values") or []], required=bool(question.get("required")),
            eeo=eeo,
        )
        for f in fields
    ]


def snapshot_from_greenhouse(data: dict, url: str) -> FormSnapshot:
    questions = list(data.get("questions") or []) + list(data.get("location_questions") or [])
    fields = [field for q in questions for field in _fields(q)]
    for block in list(data.get("compliance") or []) + [{"questions": data.get("demographic_questions") or []}]:
        fields += [field for q in block.get("questions") or [] for field in _fields(q, eeo=True)]
    texts = [html_to_text(data.get("content") or ""), html_to_text(data.get("ai_disclaimer") or "")]
    texts += [f.description for f in fields if f.description]
    return FormSnapshot(url=url, page_text="\n".join(t for t in texts if t)[:PAGE_TEXT_LIMIT], fields=fields)
