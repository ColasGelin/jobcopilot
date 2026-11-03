"""LLM tailoring of the CV to one job: Claude picks and rewords bank items, the guard checks them."""

from __future__ import annotations

import json
import re
import unicodedata

import yaml
from pydantic import ValidationError

from jobcopilot.cv.bank import Bank
from jobcopilot.cv.content import CvContent, ExperienceBlock, project_item
from jobcopilot.cv.guard import (
    MAX_BULLETS_PER_EXPERIENCE, MAX_ENGINEERING, MAX_HACKATHONS, MAX_SKILL_GROUPS, MAX_SUMMARY_WORDS,
    TailorResponse, canonical_skills, check,
)
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Evaluation, Job
from jobcopilot.scoring import RETRY_SUFFIX, load_prompt
from jobcopilot.text import normalize

TAILOR_PROMPT_VERSION = "tailor_v1"
DESCRIPTION_LIMIT = 6000

_CHOICE = {
    "type": "object",
    "properties": {"source_id": {"type": "string"}, "text": {"type": "string"}},
    "required": ["source_id", "text"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["en", "es", "fr"]},
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "bullets": {"type": "array", "items": _CHOICE}},
                "required": ["id", "bullets"],
            },
        },
        "projects": {"type": "array", "items": _CHOICE},
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"group": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
                "required": ["group", "items"],
            },
        },
        "extras": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["language", "headline", "summary", "experience", "projects", "skills", "extras"],
}


class TailorError(Exception):
    """The LLM's answer broke the rules twice; nothing is stored."""

    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


def bank_for_prompt(bank: Bank) -> str:
    data = bank.model_dump(exclude={"contact", "main_headline", "section_titles"})
    for g in data["skills"]:
        g.pop("main", None)
        g.pop("main_items", None)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)


def _render(job: Job, evaluation: Evaluation | None, bank: Bank) -> str:
    values = {
        "bank": bank_for_prompt(bank),
        "company": job.company,
        "title": job.title,
        "location": job.location or "not stated",
        "description": job.description[:DESCRIPTION_LIMIT],
        "fit_reasons": evaluation.fit_reasons if evaluation else "not scored",
        "gaps": evaluation.gaps if evaluation else "not scored",
        "max_summary_words": str(MAX_SUMMARY_WORDS),
        "max_bullets": str(MAX_BULLETS_PER_EXPERIENCE),
        "max_engineering": str(MAX_ENGINEERING),
        "max_hackathons": str(MAX_HACKATHONS),
        "max_skill_groups": str(MAX_SKILL_GROUPS),
        "experience_ids": json.dumps([e.id for e in bank.experience]),
        "headlines": json.dumps(bank.headlines, ensure_ascii=False),
    }
    prompt = load_prompt(TAILOR_PROMPT_VERSION)
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt


def to_content(resp: TailorResponse, bank: Bank) -> CvContent:
    """Build the renderable CV: wording from the response, fixed facts (titles, dates, names) from the bank."""
    experiences = {e.id: e for e in bank.experience}
    projects = {p.id: p for p in bank.projects}
    extras = {x.id: x for x in bank.extras}
    chosen = [(projects[c.source_id], c.text) for c in resp.projects]
    return CvContent(
        language=resp.language,
        contact=bank.contact,
        headline=resp.headline,
        summary=resp.summary,
        experience=[
            ExperienceBlock(title=experiences[e.id].title, company=experiences[e.id].company,
                            location=experiences[e.id].location, dates=experiences[e.id].dates,
                            bullets=[b.text for b in e.bullets])
            for e in resp.experience
        ],
        engineering=[project_item(p, text) for p, text in chosen if p.section == "engineering"],
        hackathons=[project_item(p, text) for p, text in chosen if p.section == "hackathons"],
        skills=canonical_skills(resp, bank),
        education=[e for e in bank.education if e.main],
        certifications=[c for c in bank.certifications if c.main],
        languages=bank.languages,
        other=[extras[x].text for x in resp.extras if extras[x].section == "other"],
        interests=[extras[x].text for x in resp.extras if extras[x].section == "interests"],
        section_titles=bank.section_titles,
    )


def tailor_cv(job: Job, evaluation: Evaluation | None, bank: Bank, llm: LLMClient, model: str) -> CvContent:
    prompt = _render(job, evaluation, bank)
    violations: list[str] = []
    for attempt in range(2):
        try:
            resp = TailorResponse.model_validate(llm.complete_json(prompt, SCHEMA, model))
            violations = check(resp, bank)
        except LLMUnavailable:
            raise
        except (LLMError, ValidationError) as e:
            violations = [str(e)[:500]]
        if not violations:
            return to_content(resp, bank)
        if attempt == 0:
            prompt += RETRY_SUFFIX.format(error="it broke these rules: " + "; ".join(violations))
    raise TailorError(violations)


def cv_filename(job: Job) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(job.company)).strip("-")[:40].strip("-") or "company"
    return f"{job.id}-{slug}.pdf"


def download_name(name: str) -> str:
    """What the recruiter sees: "First-Last-CV.pdf", never a job id or company."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    parts = re.findall(r"[A-Za-z0-9]+", ascii_name)
    return "-".join(parts + ["CV"]) + ".pdf"
