import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.db import DB
from jobcopilot.llm import FakeLLMClient, LLMUnavailable
from jobcopilot.models import Draft, Evaluation
from jobcopilot.web import create_app

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"

GOOD = {
    "language": "en", "headline": "AI Engineer", "summary": "AI engineer building RAG pipelines.",
    "experience": [{"id": "acme", "bullets": [{"source_id": "acme.rag", "text": "Built a RAG assistant."}]},
                   {"id": "beta", "bullets": [{"source_id": "beta.api", "text": "Built FastAPI services."}]}],
    "projects": [], "skills": [], "extras": [],
}


@pytest.fixture
def home(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    job = make_job(1, company="Acme")
    db = DB(tmp_path / "jobs.db")
    db.insert_jobs([job], NOW)
    db.save_evaluation(Evaluation(job_id=job.id, score=85, fit_reasons="fit", gaps="gap", model="m", prompt_version="v"))
    db.save_draft(Draft(job_id=job.id, cv_tweaks=["t"], cover_note="c", outreach_message="o", contact_hint="h", model="m", prompt_version="v"))
    return tmp_path, job


def client(home_dir, responses=(), htmx=True):
    llm = FakeLLMClient(list(responses))
    app = create_app(home_dir / "jobs.db", clock=lambda: NOW, cv_path=home_dir / "cv.yaml", cvs_dir=home_dir / "cvs",
                     llm_factory=lambda: llm)
    headers = {"HX-Request": "true"} if htmx else {}
    return TestClient(app, base_url="http://localhost", headers=headers), llm, app


def test_cv_page_and_pdf(home):
    tmp, _ = home
    c, _, _ = client(tmp)
    page = c.get("/cv")
    assert page.status_code == 200 and "<iframe" in page.text
    preview = c.get("/cv/preview")
    assert "Alex Martin" in preview.text and "/cv-assets/" in preview.text
    pdf = c.get("/cv.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"


def test_cv_missing_bank(home):
    tmp, _ = home
    (tmp / "cv.yaml").unlink()
    c, _, _ = client(tmp)
    page = c.get("/cv")
    assert page.status_code == 200 and "cv.yaml" in page.text
    assert c.get("/cv.pdf").status_code == 404
    assert c.get("/today").status_code == 200
    page = c.get(f"/jobs/{make_job(1, company='Acme').id}/application")
    assert re.search(r'hx-post="/jobs/[^"]+/cv"[^>]*\sdisabled', page.text)
    assert "No cv.yaml yet" in page.text


def test_invalid_bank_disables_button_with_error(home):
    tmp, _ = home
    (tmp / "cv.yaml").write_text("contact: [unclosed", encoding="utf-8")
    c, _, _ = client(tmp)
    assert c.get("/today").status_code == 200
    page = c.get(f"/jobs/{make_job(1, company='Acme').id}/application")
    assert re.search(r'hx-post="/jobs/[^"]+/cv"[^>]*\sdisabled', page.text)
    assert "not valid YAML" in page.text
    assert "not valid YAML" in c.get("/cv").text


def test_render_failure_keeps_result_and_shows_error(home, monkeypatch):
    from jobcopilot.web import app as web_app

    tmp, job = home
    c, _, _ = client(tmp, [GOOD])
    monkeypatch.setattr(web_app, "render_pdf", lambda content: (_ for _ in ()).throw(OSError("no pango")))
    r = c.post(f"/jobs/{job.id}/cv")
    assert r.status_code == 200 and "no pango" in r.text
    assert DB(tmp / "jobs.db").latest_tailored_cv(job.id) is not None


def test_stale_tailored_row_does_not_break_today(home):
    tmp, job = home
    db = DB(tmp / "jobs.db")
    db.conn.execute("INSERT INTO tailored_cvs (job_id, language, content, pages) VALUES (?, 'en', '{\"old\": 1}', 1)", (job.id,))
    db.conn.commit()
    c, _, _ = client(tmp)
    assert c.get("/today").status_code == 200


def test_today_card_has_tailor_button(home):
    tmp, job = home
    c, _, _ = client(tmp)
    assert f'href="/jobs/{job.id}/application"' in c.get("/today").text  # the card links to the job's page
    body = c.get(f"/jobs/{job.id}/application").text
    assert f'hx-post="/jobs/{job.id}/cv"' in body and "Tailor CV for this job" in body


def test_tailor_endpoint_creates_cv(home):
    tmp, job = home
    c, llm, _ = client(tmp, [GOOD])
    r = c.post(f"/jobs/{job.id}/cv")
    assert r.status_code == 200 and "Download PDF" in r.text
    assert (tmp / "cvs" / f"{job.id}-acme.pdf").exists() and len(llm.calls) == 1
    pdf = c.get(f"/jobs/{job.id}/cv.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    preview = c.get(f"/jobs/{job.id}/cv")
    assert "Built a RAG assistant." in preview.text
    assert "Download PDF" in c.get(f"/jobs/{job.id}/application").text and "Tailor it again" in r.text


def test_tailored_pdf_rerendered_when_file_missing(home):
    tmp, job = home
    c, _, _ = client(tmp, [GOOD])
    c.post(f"/jobs/{job.id}/cv")
    (tmp / "cvs" / f"{job.id}-acme.pdf").unlink()
    assert c.get(f"/jobs/{job.id}/cv.pdf").content[:4] == b"%PDF"


def test_tailored_views_404_without_cv(home):
    tmp, job = home
    c, _, _ = client(tmp)
    assert c.get(f"/jobs/{job.id}/cv").status_code == 404
    assert c.get(f"/jobs/{job.id}/cv.pdf").status_code == 404


def test_tailor_requires_htmx(home):
    tmp, job = home
    c, llm, _ = client(tmp, [GOOD], htmx=False)
    assert c.post(f"/jobs/{job.id}/cv").status_code == 403 and llm.calls == []


def test_tailor_unknown_job(home):
    tmp, _ = home
    c, _, _ = client(tmp)
    assert c.post("/jobs/nope/cv").status_code == 404


def test_tailor_lock_conflict(home):
    tmp, job = home
    c, llm, app = client(tmp, [GOOD])
    app.state.tailor_lock.acquire()
    try:
        r = c.post(f"/jobs/{job.id}/cv")
    finally:
        app.state.tailor_lock.release()
    assert r.status_code == 409 and "Already generating" in r.text and llm.calls == []


def test_tailor_error_shows_violations(home):
    tmp, job = home
    bad = GOOD | {"headline": "Wizard"}
    c, _, _ = client(tmp, [bad, bad])
    r = c.post(f"/jobs/{job.id}/cv")
    assert r.status_code == 200 and "Wizard" in r.text and "Download PDF" not in r.text


def test_tailor_llm_unavailable_message(home):
    tmp, job = home
    c, _, _ = client(tmp, [LLMUnavailable("usage limit reached")])
    r = c.post(f"/jobs/{job.id}/cv")
    assert r.status_code == 200 and "usage limit reached" in r.text


def test_tailor_missing_bank_message(home):
    tmp, job = home
    (tmp / "cv.yaml").unlink()
    c, llm, _ = client(tmp, [GOOD])
    r = c.post(f"/jobs/{job.id}/cv")
    assert r.status_code == 200 and "cv.yaml" in r.text and llm.calls == []


def test_fragment_warns_when_two_pages(home):
    tmp, job = home
    long = GOOD | {"experience": [
        {"id": "acme", "bullets": [{"source_id": b, "text": "Built things. " * 60} for b in ("acme.rag", "acme.mcp", "acme.k8s", "acme.ui")]},
        {"id": "beta", "bullets": [{"source_id": b, "text": "Built things. " * 60} for b in ("beta.api", "beta.etl", "beta.tests", "beta.docs")]},
    ]}
    c, _, _ = client(tmp, [long])
    r = c.post(f"/jobs/{job.id}/cv")
    assert "2 pages" in r.text or "3 pages" in r.text


def test_cv_assets_serves_fonts_and_rejects_traversal(home):
    tmp, _ = home
    c, _, _ = client(tmp)
    assert c.get("/cv-assets/inter-400.woff").status_code == 200
    assert c.get("/cv-assets/..%2Frender.py").status_code == 404
    assert c.get("/cv-assets/OFL-Inter.txt").status_code == 404


def test_pdf_downloads_named_after_candidate(home):
    tmp, job = home
    c, _, _ = client(tmp, [GOOD])
    assert c.get("/cv.pdf").headers["content-disposition"] == 'attachment; filename="Alex-Martin-CV.pdf"'
    c.post(f"/jobs/{job.id}/cv")
    assert c.get(f"/jobs/{job.id}/cv.pdf").headers["content-disposition"] == 'attachment; filename="Alex-Martin-CV-Acme.pdf"'


def test_cv_links_download_instead_of_opening(home):
    tmp, job = home
    c, _, _ = client(tmp, [GOOD])
    fragment = c.post(f"/jobs/{job.id}/cv").text
    assert f'href="/jobs/{job.id}/cv.pdf"' in fragment and f'href="/jobs/{job.id}/cv"' not in fragment
    assert c.get(f"/jobs/{job.id}/cv.pdf").headers["content-disposition"].startswith("attachment")
