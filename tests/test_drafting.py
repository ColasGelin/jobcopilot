import pytest

from conftest import make_job
from jobcopilot.drafting import DRAFT_PROMPT_VERSION, draft_application
from jobcopilot.llm import FakeLLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Evaluation

JOB = make_job(1, company="Acme Robotics", description="We need LangGraph agents.")
EVAL = Evaluation(job_id=JOB.id, score=82, fit_reasons="RAG + agents", gaps="No Rust", model="haiku", prompt_version="score_v1")
GOOD = {
    "cv_tweaks": ["Lead with LangGraph work", "Mention MCP servers"],
    "cover_note": "Hi Acme team, I build agentic RAG pipelines with LangGraph.",
    "outreach_message": "Hi! I build LangGraph agents for a security platform. Keen to chat about Acme.",
    "contact_hint": "CTO on LinkedIn",
    "contact_search": "Acme Robotics CTO",
}


def test_draft_success(profile):
    fake = FakeLLMClient([GOOD])
    d = draft_application(JOB, EVAL, profile, fake, "sonnet")
    assert d.job_id == JOB.id and d.cv_tweaks == GOOD["cv_tweaks"] and d.contact_hint == "CTO on LinkedIn"
    assert d.prompt_version == DRAFT_PROMPT_VERSION == "draft_v2" and d.model == "sonnet" and d.contact_search == "Acme Robotics CTO"
    prompt = fake.calls[0][0]
    assert "Acme Robotics" in prompt and "No Rust" in prompt and "Junior AI engineer" in prompt


def test_draft_retries_when_too_long(profile):
    fake = FakeLLMClient([GOOD | {"cover_note": "word " * 300}, GOOD])
    d = draft_application(JOB, EVAL, profile, fake, "sonnet")
    assert d.cover_note == GOOD["cover_note"]
    assert "previous answer was invalid" in fake.calls[1][0]


def test_outreach_must_fit_a_linkedin_connection_note(profile):
    fake = FakeLLMClient([GOOD | {"outreach_message": "x" * 301}, GOOD | {"outreach_message": "x" * 300}])
    assert len(draft_application(JOB, EVAL, profile, fake, "sonnet").outreach_message) == 300
    assert "301 characters" in fake.calls[1][0]


def test_draft_raises_after_two_failures(profile):
    with pytest.raises(LLMError):
        draft_application(JOB, EVAL, profile, FakeLLMClient([LLMError("a"), {"cv_tweaks": []}]), "sonnet")


def test_draft_unavailable_propagates(profile):
    with pytest.raises(LLMUnavailable):
        draft_application(JOB, EVAL, profile, FakeLLMClient([LLMUnavailable("x")]), "sonnet")
