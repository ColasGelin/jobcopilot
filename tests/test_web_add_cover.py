import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot.apply.models import Answer, FormField, FormSnapshot
from jobcopilot.db import DB
from jobcopilot.models import Evaluation
from jobcopilot.web import create_app

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"
HX = {"HX-Request": "true"}
AGENT = {"X-Jobcopilot-Agent": "1"}
FORM = FormSnapshot(url="https://jobs/1", fields=[
    FormField(key="cv", selector="#cv", label="Resume", kind="file"),
    FormField(key="cl", selector="#cl", label="Cover letter", kind="file"),
])


class FakePreparer:
    def __init__(self, path):
        self.path, self.added, self.covers, self.pages = path, [], [], []

    def add_job(self, url, now):
        self.added.append(url)
        if "bad" in url:
            return None, "Page failed to load: 404"
        db = DB(self.path)
        job = make_job(5, company="Linked", url=url)
        db.insert_jobs([job], now)
        app = db.create_prepared(job.id, "ready", now)
        db.update_prepared(app, now, form=FORM)
        return job.id, None

    def add_page(self, html, now, job_id=None, link=""):
        self.pages.append((html, job_id, link))
        if "broken" in html:
            return None, "The file doesn't say which page it was saved from: paste the offer's link too."
        return job_id or self.add_job(link or "https://www.linkedin.com/jobs/view/7/", now)[0], None

    def write_cover(self, job_id, now):
        self.covers.append(job_id)
        DB(self.path).save_cover_letter(job_id, "Dear Acme team,\n\nI built things.", ["cover letter: AWS not in the source"], "m", "cover_v1", now)


@pytest.fixture
def env(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    job = make_job(1, company="Acme")
    db = DB(tmp_path / "jobs.db")
    db.insert_jobs([job], NOW)
    db.save_evaluation(Evaluation(job_id=job.id, score=85, fit_reasons="f", gaps="g", model="m", prompt_version="v"))
    app = db.create_prepared(job.id, "ready", NOW)
    db.update_prepared(app, NOW, form=FORM)
    db.save_answers(app, [Answer(field_key="cv", label="Resume", kind="cv", draft="CV"),
                          Answer(field_key="cl", label="Cover letter", kind="leave")], "m", "v", NOW)
    fake = FakePreparer(tmp_path / "jobs.db")
    web = create_app(tmp_path / "jobs.db", clock=lambda: NOW, cv_path=tmp_path / "cv.yaml", cvs_dir=tmp_path / "cvs",
                     preparer_factory=lambda: fake)
    return TestClient(web, base_url="http://localhost"), db, app, job, fake


def test_today_has_add_by_link_box(env):
    c, *_ = env
    assert 'hx-post="/jobs/add"' in c.get("/today").text


def test_add_redirects_to_the_application(env):
    c, db, _, _, fake = env
    r = c.post("/jobs/add", data={"url": "https://acme.com/careers/5"}, headers=HX)
    job_id = make_job(5, url="https://acme.com/careers/5").id
    assert r.status_code == 200 and r.headers["HX-Redirect"] == f"/jobs/{job_id}/application"
    assert fake.added == ["https://acme.com/careers/5"]


def test_add_problem_shown(env):
    c, *_ = env
    r = c.post("/jobs/add", data={"url": "https://bad.example/x"}, headers=HX)
    assert "Page failed to load: 404" in r.text and "HX-Redirect" not in r.headers
    assert c.post("/jobs/add", data={"url": "https://x"}).status_code == 403


def page_file(html="<html>Saved offer</html>", name="offer.html"):
    return {"page": (name, html.encode(), "text/html")}


def test_today_has_saved_page_upload(env):
    c, *_ = env
    page = c.get("/today").text
    assert 'hx-post="/jobs/add-page"' in page and 'hx-encoding="multipart/form-data"' in page


def test_add_page_redirects_to_the_application(env):
    c, db, _, _, fake = env
    r = c.post("/jobs/add-page", files=page_file("<html>café offer</html>"), data={"link": ""}, headers=HX)
    job_id = make_job(5, url="https://www.linkedin.com/jobs/view/7/").id
    assert r.status_code == 200 and r.headers["HX-Redirect"] == f"/jobs/{job_id}/application"
    assert fake.pages == [("<html>café offer</html>", None, "")]


def test_add_page_problems_shown_and_link_kept(env):
    c, _, _, _, fake = env
    r = c.post("/jobs/add-page", files=page_file("<html>broken</html>"), data={"link": "https://acme.com/1"}, headers=HX)
    assert "paste the offer&#39;s link too" in r.text and 'value="https://acme.com/1"' in r.text and "HX-Redirect" not in r.headers
    r = c.post("/jobs/add-page", files=page_file(name="offer.pdf"), headers=HX)
    assert "Choose the .html file" in r.text and len(fake.pages) == 1  # never handed to the preparer
    assert c.post("/jobs/add-page", files=page_file()).status_code == 403


def test_add_page_refuses_huge_files(env, monkeypatch):
    import jobcopilot.web.app as web_app
    monkeypatch.setattr(web_app, "PAGE_MAX_BYTES", 10)
    c, _, _, _, fake = env
    r = c.post("/jobs/add-page", files=page_file("x" * 11), headers=HX)
    assert "larger than" in r.text and fake.pages == []


def test_saved_page_for_one_job(env):
    c, db, app, job, fake = env
    assert f'hx-post="/jobs/{job.id}/page"' in c.get(f"/jobs/{job.id}/application").text
    r = c.post(f"/jobs/{job.id}/page", files=page_file(), headers=HX)
    assert r.status_code == 200 and fake.pages == [("<html>Saved offer</html>", job.id, "")]
    assert c.post("/jobs/nope/page", files=page_file(), headers=HX).status_code == 404
    assert c.post(f"/jobs/{job.id}/page", files=page_file()).status_code == 403


def test_cover_letter_write_edit_and_pdf(env):
    c, db, app, job, fake = env
    page = c.get(f"/jobs/{job.id}/application").text
    assert f'hx-post="/jobs/{job.id}/cover"' in page and "Write cover letter" in page
    assert c.get(f"/jobs/{job.id}/cover.pdf").status_code == 404
    r = c.post(f"/jobs/{job.id}/cover", headers=HX)
    assert "Dear Acme team," in r.text and "AWS not in the source" in r.text and "Write it again" in r.text and fake.covers == [job.id]
    assert c.post(f"/jobs/{job.id}/cover/final", data={"final": "Dear Acme,\n\nMine."}, headers=HX).status_code == 204
    assert db.cover_letter(job.id)["final"] == "Dear Acme,\n\nMine."
    pdf = c.get(f"/jobs/{job.id}/cover.pdf")
    assert pdf.content[:4] == b"%PDF" and "Alex-Martin-Cover-Letter-Acme.pdf" in pdf.headers["content-disposition"]
    assert c.post(f"/jobs/{job.id}/cover").status_code == 403
    assert c.post("/jobs/nope/cover", headers=HX).status_code == 404


def test_agent_attaches_cover_letter_only_when_written(env):
    c, db, app, job, _ = env
    keys = lambda: {f["key"]: f["answer_kind"] for f in c.get(f"/api/applications/{app}", headers=AGENT).json()["fields"]}
    assert keys() == {"cv": "cv"}
    db.save_cover_letter(job.id, "Dear team", [], "m", "v", NOW)
    assert keys() == {"cv": "cv", "cl": "cover"}
    pdf = c.get(f"/api/applications/{app}/cover.pdf", headers=AGENT)
    assert pdf.content[:4] == b"%PDF" and "Cover-Letter-Acme.pdf" in pdf.headers["content-disposition"]


def test_cover_upload_row_explains_attachment(env):
    c, db, app, job, _ = env
    assert "Your cover letter is attached if you write one below" in c.get(f"/jobs/{job.id}/application").text
