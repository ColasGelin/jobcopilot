"""Read an application form in a real browser (headless on the server, visible on the Mac).

Reading never changes the page except for clicking an "Apply" button to reveal the form. Sign-in
pages and CAPTCHAs are reported, never worked around."""

from __future__ import annotations

import re
from importlib import resources

from jobcopilot.apply.models import FormField, FormSnapshot, NeedsYou

PAGE_TEXT_LIMIT = 20_000
LOAD_TIMEOUT_MS = 30_000
SETTLE_TIMEOUT_MS = 8_000
APPLY_LABELS = ("apply", "apply now", "apply for this job", "apply for this position", "postuler", "solicitar", "inscribirse")
SIGN_IN = "Sign-in or account creation required"
CAPTCHA = "CAPTCHA or bot check on the page"
NO_FORM = "No application form found"

# A page only holds an application form if some field asks for the candidate (a comment box or a search
# field alone, as on a Hacker News post, does not count).
_APPLICANT = re.compile(r"\b(?:name|e-?mail|phone|resume|cv|curriculum|linkedin|nombre|nom|prénom|correo|téléphone|teléfono)\b", re.I)

_EXTRACT_JS = resources.files("jobcopilot.apply").joinpath("extract.js").read_text(encoding="utf-8")
_APPLY = re.compile(rf"^\s*(?:{'|'.join(re.escape(a) for a in APPLY_LABELS)})\s*$", re.I)


def classify(frames: list[tuple[str, dict]], url: str) -> FormSnapshot | NeedsYou:
    """Turn what extract.js found in each frame (main frame first) into a snapshot or a reason to stop."""
    if any(raw.get("has_password") for _, raw in frames):
        return NeedsYou(reason=SIGN_IN)
    fields = [
        FormField(**f, frame="" if i == 0 else frame_url)
        for i, (frame_url, raw) in enumerate(frames)
        for f in raw.get("fields") or []
    ]
    if not fields:
        return NeedsYou(reason=CAPTCHA if any(raw.get("captcha") for _, raw in frames) else NO_FORM)
    if not any(f.kind in ("email", "tel", "file") or _APPLICANT.search(f.label) for f in fields):
        return NeedsYou(reason=NO_FORM)
    text = "\n".join(raw.get("text") or "" for _, raw in frames if raw.get("text"))
    return FormSnapshot(url=url, page_text=text[:PAGE_TEXT_LIMIT], fields=fields)


def _settle(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except Exception:  # pages that keep polling never go idle; read what is there
        pass


def _extract(page) -> list[tuple[str, dict]]:
    frames = []
    for i, frame in enumerate(page.frames):
        try:
            frames.append((frame.url, frame.evaluate(_EXTRACT_JS)))
        except Exception:  # detached or cross-process frame that won't answer
            if i == 0:
                raise
    return frames


# A control that would submit a form (submit inputs, buttons inside a form without type="button") is never
# clicked, whatever it says: a form's own "Apply" button sends the application.
_SUBMITS = """e => e.tagName === 'INPUT' || (e.tagName === 'BUTTON' && !!e.form && (e.getAttribute('type') || 'submit') !== 'button')"""


def _click_apply(page) -> bool:
    """Click a visible "Apply" link or button that reveals the form. Never one that submits a form."""
    for candidate in page.locator("a, button, [role=button]").all():
        try:
            text = candidate.inner_text(timeout=1000) or ""
            if _APPLY.match(text) and candidate.is_visible() and not candidate.evaluate(_SUBMITS):
                candidate.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def read_page(page, url: str, click_apply: bool = True) -> FormSnapshot | NeedsYou:
    """Read the form at `url` ("" = the page as it is now, e.g. after the candidate signed in)."""
    if url:
        page.goto(url, timeout=LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
        _settle(page)
    result = classify(_extract(page), page.url)
    if isinstance(result, NeedsYou) and result.reason == NO_FORM and click_apply and _click_apply(page):
        _settle(page)
        result = classify(_extract(page), page.url)
    return result
