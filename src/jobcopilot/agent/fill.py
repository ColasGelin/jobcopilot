"""Fill an application form in the visible browser. Types, selects, ticks and attaches; never clicks a button."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_OPTION_LABEL = "e => ((e.labels && e.labels[0] ? e.labels[0].innerText : '') || e.getAttribute('aria-label') || e.value || '').trim()"


@dataclass
class FillResult:
    filled: list[str] = field(default_factory=list)
    unfilled: list[dict] = field(default_factory=list)  # {key, label, why}


def _frame(page, frame_url: str):
    if not frame_url:
        return page.main_frame
    return next((f for f in page.frames if f.url == frame_url), None) or next(
        (f for f in page.frames if f.url.startswith(frame_url.split("#")[0])), None)


def _clean(text: str) -> str:
    """Same normalisation as extract.js, so option labels read and filled compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"[✱*]", " ", text or "")).strip()


def _locate(frame, f: dict):
    loc = frame.locator(f["selector"])
    if loc.count() == 0 and f.get("label"):
        loc = frame.get_by_label(f["label"], exact=True)  # the page changed since it was read
    return loc if loc.count() > 0 else None


def _find(page, f: dict):
    """(frame, locator): the recorded frame first, then every other frame (an API-read form embedded in an
    iframe, or an iframe whose URL changed since it was read)."""
    recorded = _frame(page, f.get("frame") or "")
    for frame in [recorded] + [fr for fr in page.frames if fr is not recorded]:
        if frame is None:
            continue
        loc = _locate(frame, f)
        if loc is not None:
            return frame, loc
    return None, None


def _fill_one(page, f: dict, cv_path: Path | None) -> str | None:
    """None when filled, else why not."""
    _, loc = _find(page, f)
    if loc is None:
        return "field not found on the page"
    kind, value = f["kind"], f["value"]
    if kind == "file":
        if f.get("answer_kind") != "cv":
            return "file left for you"
        if cv_path is None:
            return "no tailored CV to attach"
        loc.first.set_input_files(str(cv_path))
    elif kind == "select":
        loc.first.select_option(label=value, timeout=3000)
    elif kind in ("radio", "checkbox"):
        for option in loc.all():
            if _clean(option.evaluate(_OPTION_LABEL)) == _clean(value):
                option.check(timeout=3000)
                return None
        return f"no option labelled {value!r}"
    else:
        loc.first.fill(value, timeout=3000)
    return None


def fill_fields(page, fields: list[dict], cv_path: Path | None) -> FillResult:
    result = FillResult()
    for f in fields:
        try:
            why = _fill_one(page, f, cv_path)
        except Exception as e:  # a widget that refuses (custom dropdown, read-only input)
            why = str(e).splitlines()[0][:150]
        if why:
            result.unfilled.append({"key": f["key"], "label": f.get("label") or f["key"], "why": why})
        else:
            result.filled.append(f["key"])
    return result


def reveal_form(page, fields: list[dict]) -> None:
    """On a freshly opened job page, click "Apply" when none of the fields is on the page yet."""
    from jobcopilot.apply.browser import _click_apply, _settle

    _settle(page)
    if any(_find(page, f)[1] is not None for f in fields):
        return
    if _click_apply(page):
        _settle(page)
