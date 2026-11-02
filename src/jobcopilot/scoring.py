"""Batched LLM scoring of filtered jobs against the candidate profile."""

from __future__ import annotations

import json
from importlib import resources

from pydantic import ValidationError

from jobcopilot.config import Profile
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Evaluation, Job

SCORE_PROMPT_VERSION = "score_v1"
DESCRIPTION_LIMIT = 2000
RETRY_SUFFIX = "\n\nYour previous answer was invalid: {error}. Return only valid JSON."

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "fit_reasons": {"type": "string"},
                    "gaps": {"type": "string"},
                },
                "required": ["job_id", "score", "fit_reasons", "gaps"],
            },
        }
    },
    "required": ["results"],
}


def load_prompt(name: str) -> str:
    return resources.files("jobcopilot.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def _render(jobs: list[Job], profile: Profile) -> str:
    payload = [
        {
            "job_id": j.id,
            "company": j.company,
            "title": j.title,
            "location": j.location,
            "remote": j.remote,
            "salary": j.salary_text,
            "description": j.description[:DESCRIPTION_LIMIT],
        }
        for j in jobs
    ]
    return (
        load_prompt(SCORE_PROMPT_VERSION)
        .replace("{profile}", profile.summary)
        .replace("{jobs}", json.dumps(payload, ensure_ascii=False, indent=1))
    )


def _parse(data: dict, ids: set[str], model: str) -> list[Evaluation]:
    evals = []
    for item in data["results"]:
        if item.get("job_id") in ids:
            evals.append(Evaluation(**item, model=model, prompt_version=SCORE_PROMPT_VERSION))
    return evals


def _score_batch(batch: list[Job], profile: Profile, llm: LLMClient, model: str) -> list[Evaluation] | None:
    prompt = _render(batch, profile)
    ids = {j.id for j in batch}
    for attempt in range(2):
        try:
            return _parse(llm.complete_json(prompt, SCHEMA, model), ids, model)
        except LLMUnavailable:
            raise
        except (LLMError, ValidationError, KeyError, TypeError) as e:
            if attempt == 0:
                prompt += RETRY_SUFFIX.format(error=str(e)[:500])
    return None


def score_jobs(
    jobs: list[Job], profile: Profile, llm: LLMClient, model: str, batch_size: int = 10
) -> tuple[list[Evaluation], list[str]]:
    """Score jobs in batches. Returns (evaluations, ids of jobs that could not be scored)."""
    evaluations: list[Evaluation] = []
    failed: list[str] = []
    for start in range(0, len(jobs), batch_size):
        batch = jobs[start : start + batch_size]
        result = _score_batch(batch, profile, llm, model)
        if result is None:
            failed.extend(j.id for j in batch)
            continue
        scored = {e.job_id for e in result}
        evaluations.extend(result)
        failed.extend(j.id for j in batch if j.id not in scored)
    return evaluations, failed
