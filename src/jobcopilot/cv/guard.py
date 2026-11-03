"""Deterministic checks on a tailoring response: structure, limits, and no invented facts."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from jobcopilot.cv.bank import Bank, SkillGroup

MAX_SUMMARY_WORDS = 60
MAX_BULLETS_PER_EXPERIENCE = 4
MAX_ENGINEERING = 3
MAX_HACKATHONS = 2
MAX_SKILL_GROUPS = 5

# Digits with optional separators: "2,000", "2.000", "2 000" (space / no-break space before 3 digits), "3.5", "85 %".
_SPACES = " \u00a0\u202f"
_NUMBER = re.compile(rf"\d+(?:(?:[.,]|[{_SPACES}](?=\d{{3}}(?!\d)))\d+)*(?:[{_SPACES}]?%)?")
_THOUSANDS = re.compile(rf"^\d{{1,3}}(?:[.,{_SPACES}]\d{{3}})+$")
# Technology names that are also ordinary words ("go live", "react fast"): the source must spell them
# with the same case, or a lowercase word in the source would excuse an invented technology.
_AMBIGUOUS = {"Go", "Rust", "Express", "React", "Spring", "Swift", "Spark", "Vue", "Llama", "Flask", "Celery",
              "Helm", "Ruby", "Dart", "Julia", "Claude", "Gemini", "Whisper", "Transformers", "Jest", "Loki",
              "Chroma", "Polars", "Rails", "Make", "Chef"}


class BulletChoice(BaseModel):
    source_id: str
    text: str


class ExpChoice(BaseModel):
    id: str
    bullets: list[BulletChoice]


class TailorResponse(BaseModel):
    language: Literal["en", "es", "fr"]
    headline: str
    summary: str
    experience: list[ExpChoice]
    projects: list[BulletChoice] = []
    skills: list[SkillGroup] = []
    extras: list[str] = []


@lru_cache(maxsize=1)
def _builtin_terms() -> frozenset[str]:
    lines = (Path(__file__).parent / "tech_terms.txt").read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def tech_terms(bank: Bank) -> set[str]:
    return set(_builtin_terms()) | {item for g in bank.skills for item in g.items}


def _numbers(text: str) -> set[str]:
    out = set()
    for raw in _NUMBER.findall(text):
        pct = raw.endswith("%")
        n = raw.rstrip("%").rstrip(_SPACES)
        n = re.sub(rf"[.,{_SPACES}]", "", n) if _THOUSANDS.match(n) else n.replace(",", ".")
        out.add(n + ("%" if pct else ""))
    return out


def _term_pattern(term: str, flags: int = 0) -> re.Pattern:
    # A one-letter term (C) must not match French elision ("C'est").
    after = "A-Za-z0-9+#'\u2019" if len(term) == 1 else "A-Za-z0-9+#"
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![{after}])", flags)


def _in_source(term: str, source: str) -> bool:
    exact = term in _AMBIGUOUS or len(term) <= 2
    return bool(_term_pattern(term, 0 if exact else re.I).search(source))


def _facts(label: str, text: str, source: str, terms: set[str]) -> list[str]:
    """Numbers and technology names in `text` must already be in `source`.

    Terms are detected in the output case-sensitively (so the verb "go" or "express" is not the
    language Go or the framework Express) and looked up in the source case-insensitively, except
    ambiguous and very short terms, which need the same case there too.
    """
    problems = []
    new_numbers = _numbers(text) - _numbers(source)
    if new_numbers:
        problems.append(f"{label}: numbers {sorted(new_numbers)} are not in the source")
    invented = sorted(t for t in terms if _term_pattern(t).search(text) and not _in_source(t, source))
    if invented:
        problems.append(f"{label}: {', '.join(invented)} not in the source")
    return problems


def _bank_text(bank: Bank) -> str:
    parts = [bank.summary]
    parts += [f"{e.title} {e.company} {b.text}" for e in bank.experience for b in e.bullets]
    parts += [f"{p.name} {p.text}" for p in bank.projects]
    parts += [" ".join(g.items) for g in bank.skills]
    parts += [x.text for x in bank.extras]
    parts += [f"{e.school} {e.degree}" for e in bank.education]
    parts += [f"{c.name} {c.detail}" for c in bank.certifications]
    return "\n".join(parts)


def check_text(label: str, text: str, bank: Bank, extra_source: str = "") -> list[str]:
    """Free text (e.g. an application answer): its numbers and tech terms must be in the bank or `extra_source`."""
    dates = " ".join([e.dates for e in bank.experience] + [e.dates for e in bank.education] + [c.date for c in bank.certifications])
    return _facts(label, text, "\n".join([_bank_text(bank), dates, extra_source]), tech_terms(bank))


def _check_experience(resp: TailorResponse, bank: Bank, terms: set[str]) -> list[str]:
    problems = []
    expected = [e.id for e in bank.experience]
    got = [e.id for e in resp.experience]
    if got != expected:
        problems.append(f"experience must list {expected} exactly once, in this order; got {got}")
    owner = bank.bullet_owner()
    seen: set[str] = set()
    for exp in resp.experience:
        if not exp.bullets:
            problems.append(f"experience {exp.id!r} needs at least one bullet")
        if len(exp.bullets) > MAX_BULLETS_PER_EXPERIENCE:
            problems.append(f"experience {exp.id!r} has {len(exp.bullets)} bullets; maximum is {MAX_BULLETS_PER_EXPERIENCE}")
        for b in exp.bullets:
            if not b.text.strip():
                problems.append(f"bullet {b.source_id!r} has empty text")
            if b.source_id in seen:
                problems.append(f"bullet {b.source_id!r} is used twice")
                continue
            seen.add(b.source_id)
            if b.source_id not in owner:
                problems.append(f"bullet {b.source_id!r} does not exist")
            elif owner[b.source_id] != exp.id:
                problems.append(f"bullet {b.source_id!r} belongs to {owner[b.source_id]!r}, not {exp.id!r}")
            else:
                problems += _facts(f"bullet {b.source_id!r}", b.text, bank.item(b.source_id).text, terms)
    return problems


def _check_projects(resp: TailorResponse, bank: Bank, terms: set[str]) -> list[str]:
    problems = []
    projects = {p.id: p for p in bank.projects}
    seen: set[str] = set()
    counts = {"engineering": 0, "hackathons": 0}
    for choice in resp.projects:
        p = projects.get(choice.source_id)
        if p is None:
            problems.append(f"project {choice.source_id!r} does not exist")
            continue
        if choice.source_id in seen:
            problems.append(f"project {choice.source_id!r} is used twice")
            continue
        seen.add(choice.source_id)
        if not choice.text.strip():
            problems.append(f"project {p.id!r} has empty text")
        counts[p.section] += 1
        problems += _facts(f"project {p.id!r}", choice.text, f"{p.name} {p.text}", terms)
    if counts["engineering"] > MAX_ENGINEERING:
        problems.append(f"{counts['engineering']} engineering projects; maximum is {MAX_ENGINEERING}")
    if counts["hackathons"] > MAX_HACKATHONS:
        problems.append(f"{counts['hackathons']} hackathons; maximum is {MAX_HACKATHONS}")
    return problems


def _check_skills(resp: TailorResponse, bank: Bank) -> list[str]:
    problems = []
    groups = {g.group.lower(): g for g in bank.skills}
    if len(resp.skills) > MAX_SKILL_GROUPS:
        problems.append(f"{len(resp.skills)} skill groups; maximum is {MAX_SKILL_GROUPS}")
    seen_groups: set[str] = set()
    for g in resp.skills:
        known = groups.get(g.group.lower())
        if known is None:
            problems.append(f"skill group {g.group!r} does not exist")
            continue
        if known.group in seen_groups:
            problems.append(f"skill group {known.group!r} is listed twice")
        seen_groups.add(known.group)
        if len({i.lower() for i in g.items}) != len(g.items):
            problems.append(f"an item is listed twice in skill group {known.group!r}")
        allowed = {i.lower() for i in known.items}
        unknown = [i for i in g.items if i.lower() not in allowed]
        if unknown:
            problems.append(f"skills {unknown} are not in group {known.group!r}")
    return problems


def check(resp: TailorResponse, bank: Bank) -> list[str]:
    """Every rule violation, in plain words; an empty list means the response can be used."""
    terms = tech_terms(bank)
    problems = []
    if resp.headline not in bank.headlines:
        problems.append(f"headline {resp.headline!r} is not one of {bank.headlines}")
    words = len(resp.summary.split())
    if words > MAX_SUMMARY_WORDS:
        problems.append(f"summary has {words} words; maximum is {MAX_SUMMARY_WORDS}")
    problems += _facts("summary", resp.summary, _bank_text(bank), terms)
    problems += _check_experience(resp, bank, terms)
    problems += _check_projects(resp, bank, terms)
    problems += _check_skills(resp, bank)
    extras = {x.id for x in bank.extras}
    for x in resp.extras:
        if x not in extras:
            problems.append(f"extra {x!r} does not exist")
    if len(set(resp.extras)) != len(resp.extras):
        problems.append("an extra is listed twice")
    return problems


def canonical_skills(resp: TailorResponse, bank: Bank) -> list[SkillGroup]:
    """The response's skills with the bank's spelling (call after `check` passed)."""
    out = []
    for g in resp.skills:
        known = next(k for k in bank.skills if k.group.lower() == g.group.lower())
        spelling = {i.lower(): i for i in known.items}
        out.append(SkillGroup(group=known.group, items=[spelling[i.lower()] for i in g.items]))
    return out
