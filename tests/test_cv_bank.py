from pathlib import Path

import pytest
import yaml

from jobcopilot.cv.bank import BankError, load_bank
from jobcopilot.cv.content import main_content

EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"


def write_variant(tmp_path, change) -> Path:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "cv.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_example_bank():
    bank = load_bank(EXAMPLE)
    assert bank.contact.name == "Alex Martin"
    assert [e.id for e in bank.experience] == ["acme", "beta"]
    assert sum(len(e.bullets) for e in bank.experience) == 8
    assert len(bank.projects) == 7 and len(bank.skills) == 5


def test_duplicate_ids_rejected(tmp_path):
    path = write_variant(tmp_path, lambda d: d["projects"][0].update(id="acme.rag"))
    with pytest.raises(BankError, match="acme.rag"):
        load_bank(path)


def test_bad_status_rejected(tmp_path):
    path = write_variant(tmp_path, lambda d: d["projects"][0].update(status="maybe"))
    with pytest.raises(BankError):
        load_bank(path)


def test_missing_file_raises_bank_error(tmp_path):
    with pytest.raises(BankError, match="cv.yaml"):
        load_bank(tmp_path / "cv.yaml")


def test_invalid_yaml_raises_bank_error(tmp_path):
    path = tmp_path / "cv.yaml"
    path.write_text("contact: [unclosed", encoding="utf-8")
    with pytest.raises(BankError):
        load_bank(path)


def test_main_headline_must_be_listed(tmp_path):
    path = write_variant(tmp_path, lambda d: d.update(main_headline="Astronaut"))
    with pytest.raises(BankError, match="Astronaut"):
        load_bank(path)


def test_main_content_selects_main_items():
    content = main_content(load_bank(EXAMPLE))
    assert content.headline == "AI Engineer"
    assert content.experience[0].bullets == [
        "Built a RAG assistant over 2,000 support articles with LangChain and Qdrant.",
        "Wrote an MCP server so agents can query the PostgreSQL database and create reports.",
        "Deployed every service on Kubernetes with Docker and a GitLab CI pipeline.",
    ]
    assert [p.name for p in content.engineering] == ["Cluster Lab", "Neural Net"]
    assert [p.name for p in content.hackathons] == ["1st place · City Hackathon"]
    assert content.other == ["Lifeguard at a city pool (summers 2021–2022)."]
    assert content.interests == ["Chess, climbing"]
    assert len(content.skills) == 5


def test_in_progress_flag_carried(tmp_path):
    path = write_variant(tmp_path, lambda d: d["projects"][3].update(main=True))
    content = main_content(load_bank(path))
    agent = next(p for p in content.engineering if p.name == "Agent Lab")
    assert agent.in_progress is True


def test_bullet_owner():
    owner = load_bank(EXAMPLE).bullet_owner()
    assert owner["acme.mcp"] == "acme" and owner["beta.etl"] == "beta"


def test_main_skills_use_main_items_and_skip_non_main_groups(tmp_path):
    def change(d):
        d["skills"][0]["main_items"] = ["RAG", "MCP"]
        d["skills"][4]["main"] = False
        d["education"].append({"id": "bac", "school": "Lycée", "degree": "Baccalauréat", "main": False})
    content = main_content(load_bank(write_variant(tmp_path, change)))
    assert content.skills[0].items == ["RAG", "MCP"]
    assert [g.group for g in content.skills] == ["LLM systems", "Backend", "Infrastructure", "Frontend"]
    assert [e.school for e in content.education] == ["Code School"]


def test_main_items_must_be_in_items(tmp_path):
    path = write_variant(tmp_path, lambda d: d["skills"][0].update(main_items=["Cobol"]))
    with pytest.raises(BankError, match="Cobol"):
        load_bank(path)
