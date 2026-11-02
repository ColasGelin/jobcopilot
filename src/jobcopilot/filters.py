"""Deterministic hard filters applied before any LLM call."""

import re

from jobcopilot.config import HardFilters
from jobcopilot.models import Job
from jobcopilot.text import normalize

_NUMBER = re.compile(r"(\d[\d,.]*)\s*(k)?\b", re.IGNORECASE)


def _contains_word(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(normalize(needle))}(?!\w)", haystack) is not None


def _max_salary(text: str) -> int | None:
    values = []
    for digits, k in _NUMBER.findall(text):
        digits = digits.replace(",", "").rstrip(".")
        try:
            value = float(digits)
        except ValueError:
            continue
        values.append(int(value * 1000) if k else int(value))
    return max(values) if values else None


def rejection_reason(job: Job, f: HardFilters) -> str | None:
    """Return why the job is rejected, or None if it passes."""
    title = normalize(job.title)
    for kw in f.exclude_title_keywords:
        if _contains_word(title, kw):
            return f"title:{kw}"

    text = normalize(f"{job.title} {job.description}")
    for pattern in f.exclude_text_patterns:
        if normalize(pattern) in text:
            return f"text:{pattern}"

    location = normalize(job.location)
    remote_ok = job.remote and (not location or any(_contains_word(location, r) for r in f.remote_regions))
    onsite_ok = any(_contains_word(location, c) for c in f.onsite_cities)
    if not (remote_ok or onsite_ok):
        return "location"

    if f.min_salary_eur and job.salary_text:
        top = _max_salary(job.salary_text)
        if top is not None and top < f.min_salary_eur:
            return "salary"
    return None


def apply_filters(jobs: list[Job], f: HardFilters) -> list[Job]:
    return [j for j in jobs if rejection_reason(j, f) is None]
