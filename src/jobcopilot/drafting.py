"""LLM drafting of application material for the best matches."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError, field_validator

from jobcopilot.config import Profile
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Draft, Evaluation, Job
from jobcopilot.scoring import RETRY_SUFFIX, load_prompt

DRAFT_PROMPT_VERSION = "draft_v1"
DESCRIPTION_LIMIT = 6000
# Prompt asks for 120 / 70 words; allow a little slack before forcing a retry.
MAX_COVER_WORDS = 140
MAX_OUTREACH_WORDS = 85

SCHEMA = {
    "type": "object",
    "properties": {
        "cv_tweaks": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3},
        "cover_note": {"type": "string"},
        "outreach_message": {"type": "string"},
        "contact_hint": {"type": "string"},
    },
    "required": ["cv_tweaks", "cover_note", "outreach_message", "contact_hint"],
}


class _DraftResponse(BaseModel):
    cv_tweaks: list[str]
    cover_note: str
    outreach_message: str
    contact_hint: str

    @field_validator("cover_note")
    @classmethod
    def _cover_length(cls, v: str) -> str:
        if len(v.split()) > MAX_COVER_WORDS:
            raise ValueError(f"cover_note has {len(v.split())} words; maximum is 120")
        return v

    @field_validator("outreach_message")
    @classmethod
    def _outreach_length(cls, v: str) -> str:
        if len(v.split()) > MAX_OUTREACH_WORDS:
            raise ValueError(f"outreach_message has {len(v.split())} words; maximum is 70")
        return v


def _render(job: Job, evaluation: Evaluation, profile: Profile) -> str:
    values = {
        "profile": profile.summary,
        "company": job.company,
        "title": job.title,
        "location": job.location or "not stated",
        "salary": job.salary_text or "not stated",
        "url": job.url,
        "description": job.description[:DESCRIPTION_LIMIT],
        "fit_reasons": evaluation.fit_reasons,
        "gaps": evaluation.gaps,
    }
    prompt = load_prompt(DRAFT_PROMPT_VERSION)
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt


def draft_application(job: Job, evaluation: Evaluation, profile: Profile, llm: LLMClient, model: str) -> Draft:
    prompt = _render(job, evaluation, profile)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            resp = _DraftResponse.model_validate(llm.complete_json(prompt, SCHEMA, model))
            return Draft(job_id=job.id, model=model, prompt_version=DRAFT_PROMPT_VERSION, **resp.model_dump())
        except LLMUnavailable:
            raise
        except (LLMError, ValidationError) as e:
            last_error = e
            if attempt == 0:
                prompt += RETRY_SUFFIX.format(error=str(e)[:500])
    raise LLMError(f"drafting failed for {job.id}: {last_error}")
