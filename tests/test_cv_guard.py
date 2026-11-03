from pathlib import Path

import pytest

from jobcopilot.cv.bank import SkillGroup, load_bank
from jobcopilot.cv.guard import BulletChoice, ExpChoice, TailorResponse, canonical_skills, check

EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"


@pytest.fixture
def bank():
    return load_bank(EXAMPLE)


def valid(**overrides) -> TailorResponse:
    data = dict(
        language="en",
        headline="Backend Engineer (Python)",
        summary="AI engineer building RAG pipelines with Python and LangGraph, shipping to Kubernetes.",
        experience=[
            ExpChoice(id="acme", bullets=[
                BulletChoice(source_id="acme.mcp", text="Built an MCP server letting agents query PostgreSQL and create reports."),
                BulletChoice(source_id="acme.rag", text="Built a RAG assistant over 2,000 support articles (LangChain, Qdrant)."),
            ]),
            ExpChoice(id="beta", bullets=[BulletChoice(source_id="beta.api", text="Built FastAPI services for 3 internal teams.")]),
        ],
        projects=[
            BulletChoice(source_id="k8s-lab", text="Kubernetes cluster on K3s with Argo CD."),
            BulletChoice(source_id="hack1", text="Air-quality map in React and Python, built in 48 hours."),
        ],
        skills=[SkillGroup(group="Backend", items=["Python", "FastAPI"]), SkillGroup(group="LLM systems", items=["RAG"])],
        extras=["chess"],
    )
    return TailorResponse(**(data | overrides))


def test_guard_accepts_valid(bank):
    assert check(valid(), bank) == []


def test_guard_accepts_translation(bank):
    resp = valid(language="es")
    resp.experience[1].bullets[0].text = "Desarrollé servicios FastAPI usados por 3 equipos internos."
    assert check(resp, bank) == []


def test_guard_rejects_invented_tech(bank):
    resp = valid()
    resp.experience[1].bullets[0].text = "Built FastAPI services on Kubernetes for 3 internal teams."
    problems = check(resp, bank)
    assert len(problems) == 1 and "Kubernetes" in problems[0] and "beta.api" in problems[0]


def test_guard_rejects_tech_not_in_bank_at_all(bank):
    resp = valid()
    resp.experience[1].bullets[0].text = "Built FastAPI services in Rust for 3 internal teams."
    assert any("Rust" in p for p in check(resp, bank))


def test_guard_rejects_invented_number(bank):
    resp = valid()
    resp.experience[1].bullets[0].text = "Built FastAPI services used by 12 internal teams."
    assert any("12" in p for p in check(resp, bank))


def test_guard_rejects_unknown_source_id(bank):
    resp = valid()
    resp.experience[0].bullets[0].source_id = "acme.nope"
    assert any("acme.nope" in p for p in check(resp, bank))


def test_guard_rejects_foreign_bullet(bank):
    resp = valid()
    resp.experience[0].bullets.append(BulletChoice(source_id="beta.etl", text="Wrote ETL jobs in Python and SQL."))
    assert any("beta.etl" in p for p in check(resp, bank))


def test_guard_rejects_missing_experience(bank):
    resp = valid()
    resp.experience = resp.experience[:1]
    assert any("beta" in p for p in check(resp, bank))


def test_guard_rejects_reordered_experience(bank):
    resp = valid()
    resp.experience = list(reversed(resp.experience))
    assert check(resp, bank)


def test_guard_rejects_empty_experience(bank):
    resp = valid()
    resp.experience[1].bullets = []
    assert any("beta" in p for p in check(resp, bank))


def test_guard_rejects_headline_not_listed(bank):
    assert any("headline" in p for p in check(valid(headline="Chief AI Officer"), bank))


def test_guard_rejects_unknown_skill(bank):
    resp = valid(skills=[SkillGroup(group="Backend", items=["Python", "Go"])])
    assert any("Go" in p for p in check(resp, bank))


def test_guard_rejects_unknown_skill_group(bank):
    resp = valid(skills=[SkillGroup(group="Cloud", items=["Docker"])])
    assert any("Cloud" in p for p in check(resp, bank))


def test_guard_skill_case_insensitive(bank):
    resp = valid(skills=[SkillGroup(group="backend", items=["python", "fastapi"])])
    assert check(resp, bank) == []
    assert canonical_skills(resp, bank) == [SkillGroup(group="Backend", items=["Python", "FastAPI"])]


def test_guard_rejects_unknown_project_and_extra(bank):
    resp = valid(extras=["skydiving"])
    resp.projects.append(BulletChoice(source_id="moonshot", text="x"))
    problems = check(resp, bank)
    assert any("skydiving" in p for p in problems) and any("moonshot" in p for p in problems)


def test_guard_rejects_duplicates(bank):
    resp = valid()
    resp.projects.append(resp.projects[0])
    assert any("k8s-lab" in p for p in check(resp, bank))


def test_guard_rejects_over_limits(bank):
    resp = valid(summary=" ".join(["word"] * 61))
    resp.experience[0].bullets = [BulletChoice(source_id=b, text="Built things.") for b in ("acme.rag", "acme.mcp", "acme.k8s", "acme.ui", "acme.ui")]
    resp.projects = [BulletChoice(source_id=p, text="Agent lab.") for p in ("k8s-lab", "mlp", "rl", "agentlab")]
    problems = " | ".join(check(resp, bank))
    assert "summary" in problems and "acme" in problems and "engineering" in problems


def test_guard_summary_checked_against_whole_bank(bank):
    ok = valid(summary="Builds MCP servers and FastAPI services; deployed with Argo CD.")
    assert check(ok, bank) == []
    bad = valid(summary="Builds MCP servers on AWS.")
    assert any("AWS" in p for p in check(bad, bank))


def test_guard_special_terms_whole_word(bank):
    resp = valid()
    # "Go" must not match inside "Google" or "going"; C++ must match literally.
    resp.experience[1].bullets[0].text = "Built FastAPI services for 3 internal teams, going beyond Google-scale ideas."
    assert check(resp, bank) == []
    resp.experience[1].bullets[0].text = "Built FastAPI services in C++ for 3 internal teams."
    assert any("C++" in p for p in check(resp, bank))


def test_guard_numbers_ignore_thousand_separator_style(bank):
    resp = valid(language="es")
    resp.experience[0].bullets[1].text = "Asistente RAG sobre 2.000 artículos de soporte (LangChain, Qdrant)."
    assert check(resp, bank) == []


def test_guard_common_words_not_mistaken_for_tech(bank):
    resp = valid()
    resp.experience[1].bullets[0].text = "Built FastAPI services to go live fast and express ideas for 3 internal teams."
    assert check(resp, bank) == []
