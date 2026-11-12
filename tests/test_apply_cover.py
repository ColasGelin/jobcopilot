from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from conftest import make_job
from jobcopilot.apply.cover import COVER_PROMPT_VERSION, MAX_WORDS, cover_filename, render_cover, write_cover_letter
from jobcopilot.apply.voice import Voice
from jobcopilot.config import ApplicationProfile
from jobcopilot.cv.bank import load_bank
from jobcopilot.db import DB
from jobcopilot.llm import FakeLLMClient

EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
VOICE = Voice(samples=["Ive built many things. The most interesting to me is the MCP server."], notes="", recent=[])
GOOD = "Dear Acme team,\n\nI built a RAG assistant with LangChain and Qdrant.\n\nBest regards,\nAlex Martin"


@pytest.fixture
def bank():
    return load_bank(EXAMPLE)


def write(bank, responses, description="Nous construisons des agents."):
    llm = FakeLLMClient(list(responses))
    job = make_job(1, company="Acme", title="LLM Engineer", description=description)
    return write_cover_letter(job, bank, ApplicationProfile(first_name="Alex"), VOICE, llm, "sonnet",
                              candidate="Looking for remote AI agent roles; loves security."), llm


def test_writes_in_voice_with_facts_from_bank(bank):
    (text, problems), llm = write(bank, [{"letter": GOOD}])
    assert text == GOOD and problems == []
    prompt = llm.calls[0][0]
    for s in ("The most interesting to me is the MCP server.", "LLM Engineer", "Acme", "Nous construisons des agents.", "acme.rag", str(MAX_WORDS)):
        assert s in prompt
    assert llm.calls[0][2] == "sonnet" and COVER_PROMPT_VERSION == "cover_v4"


def test_invented_fact_retried_then_kept_with_problems(bank):
    bad = {"letter": "Dear team,\n\nI deployed agents on AWS."}
    (text, problems), llm = write(bank, [bad, bad])
    assert len(llm.calls) == 2 and "AWS" in llm.calls[1][0] and "AWS" in problems[0] and "AWS" in text


def test_too_long_is_a_problem(bank):
    long = {"letter": "word " * (MAX_WORDS + 20)}
    (_, problems), _ = write(bank, [long, long])
    assert str(MAX_WORDS) in problems[0]


def test_storage_roundtrip(tmp_path):
    db = DB(tmp_path / "jobs.db")
    job = make_job(1)
    db.insert_jobs([job], NOW)
    assert db.cover_letter(job.id) is None
    db.save_cover_letter(job.id, "Draft", ["p"], "sonnet", "cover_v1", NOW)
    assert db.cover_letter(job.id) == {"draft": "Draft", "final": "Draft", "problems": ["p"]}
    assert db.set_cover_final(job.id, "Mine", NOW) and db.cover_letter(job.id)["final"] == "Mine"
    db.save_cover_letter(job.id, "Redo", [], "sonnet", "cover_v1", NOW)  # Redo replaces it
    assert db.cover_letter(job.id) == {"draft": "Redo", "final": "Redo", "problems": []}
    assert not db.set_cover_final("nope", "x", NOW)
    db.delete_jobs([job.id])
    assert db.cover_letter(job.id) is None


def test_pdf_and_filename(bank):
    pdf = render_cover(bank.contact, make_job(1, company="Acme", title="LLM Engineer"), GOOD, date(2026, 11, 4))
    assert pdf[:4] == b"%PDF"
    assert cover_filename("Alex Martin") == "Alex-Martin-Cover-Letter.pdf"
    assert cover_filename("Alex Martin", "Acme") == "Alex-Martin-Cover-Letter-Acme.pdf"


def test_prompt_asks_why_this_company_and_role(bank):
    _, llm = write(bank, [{"letter": GOOD}])
    prompt = llm.calls[0][0]
    assert "Looking for remote AI agent roles; loves security." in prompt
    for s in ("why this role", "own words", "never present", "you are looking for"):
        assert s in prompt.lower()


def test_company_facts_from_the_posting_are_allowed(bank):
    letter = {"letter": "Dear Acme team,\n\nYou run agents on Kubernetes for 400 customers, and I built a RAG assistant."}
    (_, problems), _ = write(bank, [letter], description="We run agents on Kubernetes for 400 customers.")
    assert problems == []


def test_letter_complements_the_form_answers(bank):
    llm = FakeLLMClient([{"letter": "Dear Acme team,\n\nI built a RAG assistant over 2,000 support articles with LangChain and Qdrant.\n\nAlex Martin"}] * 2)
    answers = [("Why Acme?", "I built a RAG assistant over 2,000 support articles with LangChain and Qdrant last year.")]
    text, problems = write_cover_letter(make_job(1, company="Acme"), bank, ApplicationProfile(), VOICE, llm, "sonnet", answers=answers)
    assert "Why Acme?" in llm.calls[0][0] and "do not repeat" in llm.calls[0][0].lower()
    assert any("repeats your answer to \"Why Acme?\"" in p for p in problems) and len(llm.calls) == 2


def test_copying_the_posting_word_for_word_is_flagged(bank):
    posting = "You will design and ship agentic workflows that help our customers automate their support operations."
    letter = {"letter": "Dear Acme team,\n\nI want to design and ship agentic workflows that help our customers automate things.\n\nAlex"}
    (_, problems), llm = write(bank, [letter, letter], description=posting)
    assert any("copies the job posting" in p for p in problems) and len(llm.calls) == 2


def test_ai_sounding_letter_is_rewritten_once(bank):
    tell = {"letter": "Dear Acme team,\n\nThe hard part is not the demo, it's the trust. I built a RAG assistant with LangChain and Qdrant.\n\nAlex Martin"}
    (text, problems), llm = write(bank, [tell, {"letter": GOOD}])
    assert len(llm.calls) == 2 and "not X, it's Y" in llm.calls[1][0] and text == GOOD and problems == []
    (text, problems), _ = write(bank, [tell, tell])  # still there after the retry: kept, with the problem shown
    assert "not the demo, it's" in problems[0]
