"""Spot an answer or a cover letter that repeats another part of the same application word for word."""

from __future__ import annotations

import re

MIN_WORDS = 8  # a shared passage this long reads as copy-paste to a recruiter

_WORD = re.compile(r"[\w'’]+")


def _shared(text: str, other: str) -> str | None:
    """The first passage of MIN_WORDS+ words of `text` that also appears in `other` (case and punctuation ignored)."""
    words = _WORD.findall(text)
    low = [w.lower() for w in words]
    haystack = " " + " ".join(w.lower() for w in _WORD.findall(other)) + " "
    for i in range(len(low) - MIN_WORDS + 1):
        if " " + " ".join(low[i:i + MIN_WORDS]) + " " in haystack:
            j = i + MIN_WORDS
            while j < len(low) and " " + " ".join(low[i:j + 1]) + " " in haystack:
                j += 1
            return " ".join(words[i:j])
    return None


def copied(label: str, text: str, posting: str) -> list[str]:
    """A passage lifted word for word from the job posting reads as mechanical: it should be rephrased."""
    passage = _shared(text, posting) if posting else None
    return [f'{label} copies the job posting word for word: "{passage}"; say it in your own words'] if passage else []


def repeats(label: str, text: str, answers: list[tuple[str, str]], letter: str = "") -> list[str]:
    """Problems for `text` (named `label`) repeating one of the other `answers` (question, text) or the cover letter."""
    problems = []
    for question, other in answers:
        if passage := _shared(text, other):
            problems.append(f'{label} repeats your answer to "{question}": "{passage}"')
    if letter and (passage := _shared(text, letter)):
        problems.append(f'{label} repeats your cover letter: "{passage}"')
    return problems
