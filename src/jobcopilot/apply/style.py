"""Spot the habits that make a cover letter or an answer read as AI-written to a recruiter.

Plain patterns, not a detector: each problem names the phrase and what to do instead, so Claude can rewrite it on
the retry and the candidate sees what is left. English, Spanish and French, the languages postings come in."""

from __future__ import annotations

import re

# "the hard part is not X, it's Y", "isn't just X, it's Y", "a compliance problem, not a small bug", "no es X, es Y",
# "ce n'est pas X, c'est Y"
_CONTRAST = re.compile(
    r"\w[^.!?;\n,]{0,60},\s*not (?:a|an|just|only|merely|simply)\b[^.!?;\n]{0,40}"
    r"|\w[^.!?;\n,]{0,60},\s*no (?:un|una|solo|sólo)\b[^.!?;\n]{0,40}|\w[^.!?;\n,]{0,60},\s*pas (?:un|une|juste|seulement)\b[^.!?;\n]{0,40}|"
    r"\b(?:not|isn['’]t|wasn['’]t|aren['’]t)\b[^.!?;\n]{1,80}?[,;:]\s*(?:it['’]s|it is|that['’]s|this is|they['’]re|but rather)\b"
    r"|\bnot (?:just|only|merely|simply)\b[^.!?;\n]{1,80}?[,;:]?\s*(?:it['’]s|it is|but)\b"
    r"|\bno es\b[^.!?;\n]{1,80}?,\s*(?:es|sino)\b|\bno (?:solo|sólo)\b[^.!?;\n]{1,80}?\bsino\b"
    r"|\bn['’]est pas\b[^.!?;\n]{1,80}?,\s*c['’]est\b|\bpas (?:seulement|uniquement)\b[^.!?;\n]{1,80}?\bmais\b",
    re.I,
)
_DASH = re.compile(r"\s?—\s?|\s–\s")
# Stock phrases recruiters now read as generated text.
_STOCK = re.compile(
    r"\b(?:I(?:['’]m| am) (?:excited|thrilled|eager)|passionate|caught my (?:attention|eye)|resonates? with|delve|"
    r"leverag(?:e|ing)|fast-paced|cutting-edge|dynamic team|perfect fit|testament to|I am writing to|align(?:s|ed)? (?:perfectly )?with|"
    r"apasionad[oa]|me apasiona|me entusiasma|encaja perfectamente|"
    r"passionn[ée]e?|je suis ravie?|parfaitement en adéquation)\b",
    re.I,
)


def ai_tells(label: str, text: str) -> list[str]:
    """Problems for `text` (named `label`) that make it sound AI-written, each with the phrase found."""
    problems = []
    if m := _CONTRAST.search(text):
        problems.append(f'{label} uses the "not X, it\'s Y" pattern ("{m.group(0).strip()}"): say the point directly')
    if _DASH.search(text):
        problems.append(f"{label} uses a long dash (—): use a comma, a colon or two sentences")
    found = sorted({m.group(0).lower() for m in _STOCK.finditer(text)})
    if found:
        problems.append(f"{label} uses stock phrases ({', '.join(found)}): use plain words of your own")
    return problems
