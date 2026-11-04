from pathlib import Path

import pytest

from conftest import make_job
from jobcopilot.cv.bank import load_bank
from jobcopilot.cv.tailor import TailorError, cv_filename, tailor_cv, to_content
from jobcopilot.cv.guard import TailorResponse
from jobcopilot.llm import FakeLLMClient, LLMUnavailable
from jobcopilot.models import Evaluation

EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"


@pytest.fixture
def bank():
    return load_bank(EXAMPLE)


def response(**overrides) -> dict:
    data = {
        "language": "es",
        "headline": "Backend Engineer (Python)",
        "summary": "Ingeniero de IA que construye pipelines RAG con Python y LangGraph.",
        "experience": [
            {"id": "acme", "bullets": [{"source_id": "acme.mcp", "text": "Servidor MCP para consultar PostgreSQL y crear informes."}]},
            {"id": "beta", "bullets": [{"source_id": "beta.api", "text": "Servicios FastAPI usados por 3 equipos internos."}]},
        ],
        "projects": [
            {"source_id": "agentlab", "text": "Plataforma de agentes con LangGraph."},
            {"source_id": "hack1", "text": "Mapa de calidad del aire con React y Python en 48 horas."},
        ],
        "skills": [{"group": "backend", "items": ["python", "FastAPI"]}],
        "extras": ["lifeguard", "chess"],
    }
    return data | overrides


EVAL = Evaluation(job_id="x", score=82, fit_reasons="Strong RAG fit", gaps="No Go experience", model="m", prompt_version="v")


def test_tailor_returns_content(bank):
    llm = FakeLLMClient([response()])
    content = tailor_cv(make_job(1, company="Acme"), EVAL, bank, llm, "sonnet")
    assert content.language == "es" and content.headline == "Backend Engineer (Python)"
    assert content.experience[0].title == "AI Engineer" and content.experience[0].dates == "Jan 2025 – Present"
    assert content.experience[0].bullets == ["Servidor MCP para consultar PostgreSQL y crear informes."]
    assert content.skills[0].group == "Backend" and content.skills[0].items == ["Python", "FastAPI"]
    assert llm.calls[0][2] == "sonnet"


def test_tailor_retries_with_violations(bank):
    bad = response()
    bad["experience"][1]["bullets"][0]["text"] = "Servicios FastAPI en Rust para 3 equipos."
    llm = FakeLLMClient([bad, response()])
    tailor_cv(make_job(1), None, bank, llm, "sonnet")
    assert len(llm.calls) == 2 and "Rust" in llm.calls[1][0]


def test_tailor_retries_on_schema_error(bank):
    llm = FakeLLMClient([{"language": "de"}, response()])
    tailor_cv(make_job(1), None, bank, llm, "sonnet")
    assert len(llm.calls) == 2


def test_tailor_raises_after_two_failures(bank):
    bad = response(headline="Wizard")
    llm = FakeLLMClient([bad, bad])
    with pytest.raises(TailorError) as e:
        tailor_cv(make_job(1), None, bank, llm, "sonnet")
    assert e.value.violations and "Wizard" in e.value.violations[0]


def test_tailor_llm_unavailable_propagates(bank):
    with pytest.raises(LLMUnavailable):
        tailor_cv(make_job(1), None, bank, FakeLLMClient([LLMUnavailable("logged out")]), "sonnet")


def test_tailor_prompt_contains_bank_and_job(bank):
    llm = FakeLLMClient([response()])
    tailor_cv(make_job(1, company="Acme", title="LLM Engineer", description="We use LangGraph daily."), EVAL, bank, llm, "sonnet")
    prompt = llm.calls[0][0]
    for text in ("acme.mcp", "Lifeguard", "LLM Engineer", "We use LangGraph daily.", "Strong RAG fit", "No Go experience"):
        assert text in prompt
    assert "{" + "bank}" not in prompt


def test_to_content_splits_sections(bank):
    content = to_content(TailorResponse(**response()), bank)
    assert [p.name for p in content.engineering] == ["Agent Lab"] and content.engineering[0].in_progress
    assert [p.name for p in content.hackathons] == ["1st place · City Hackathon"]
    assert content.other == ["Lifeguard at a city pool (summers 2021–2022)."] and content.interests == ["Chess, climbing"]
    assert content.contact.name == "Alex Martin" and content.education[0].school == "Code School"


def test_cv_filename_slug():
    job = make_job(1, company="Ünïcorn  AI / Labs, S.L.")
    assert cv_filename(job) == f"{job.id}-unicorn-ai-labs-s-l.pdf"
    assert cv_filename(make_job(2, company="")) == f"{make_job(2).id}-company.pdf"


def test_download_name_uses_candidate_name():
    from jobcopilot.cv.tailor import download_name

    assert download_name("Alex Martin") == "Alex-Martin-CV.pdf"
    assert download_name("Zoë  O'Brien-Núñez") == "Zoe-O-Brien-Nunez-CV.pdf"
    assert download_name("  ") == "CV.pdf"


def test_download_name_with_company():
    from jobcopilot.cv.tailor import download_name

    assert download_name("Alex Martin", "Acme") == "Alex-Martin-CV-Acme.pdf"
    assert download_name("Alex Martin", "Ünïcorn AI / Labs, S.L.") == "Alex-Martin-CV-Unicorn-AI-Labs-S-L.pdf"
    assert download_name("Alex Martin", "  ") == "Alex-Martin-CV.pdf"
