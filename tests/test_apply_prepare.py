import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import make_job
from jobcopilot.apply.models import FormField, FormSnapshot, NeedsYou
from jobcopilot.apply.prepare import Preparer
from jobcopilot.config import ApplicationProfile, HardFilters, Profile, Settings
from jobcopilot.cv.bank import load_bank
from jobcopilot.db import DB
from jobcopilot.llm import FakeLLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Evaluation

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"
TAILORED = {
    "language": "en", "headline": "AI Engineer", "summary": "AI engineer building RAG pipelines.",
    "experience": [{"id": "acme", "bullets": [{"source_id": "acme.rag", "text": "Built a RAG assistant."}]},
                   {"id": "beta", "bullets": [{"source_id": "beta.api", "text": "Built FastAPI services."}]}],
    "projects": [], "skills": [], "extras": [],
}
FIELDS = [FormField(key="email", selector="#email", label="Email", kind="email"),
          FormField(key="why", selector="#why", label="Why us?", kind="textarea"),
          FormField(key="cv", selector="#cv", label="Resume/CV", kind="file")]
FORM = FormSnapshot(url="https://jobs/1", page_text="Join Acme.", fields=FIELDS)
DRAFT = {"answers": [{"key": "email", "kind": "fact", "profile_key": "email"},
                     {"key": "why", "kind": "open", "answer": "I built a RAG assistant."}, {"key": "cv", "kind": "leave"}]}


@pytest.fixture
def env(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    db = DB(tmp_path / "jobs.db")
    jobs = [make_job(i, company=f"Co{i}") for i in (1, 2, 3)]
    db.insert_jobs(jobs, NOW)
    for job, score in zip(jobs, (90, 80, 50)):
        db.save_evaluation(Evaluation(job_id=job.id, score=score, fit_reasons="", gaps="", model="m", prompt_version="v"))
    settings = Settings(data_dir=tmp_path)
    profile = Profile(hard_filters=HardFilters(), summary="s", application=ApplicationProfile(email="alex@example.com"))
    return tmp_path, db, settings, profile, jobs


def preparer(env, responses, read=None):
    tmp, db, settings, profile, _ = env
    reads = []

    def default_read(job):
        reads.append(job.id)
        return FORM

    llm = FakeLLMClient(list(responses))
    p = Preparer(db=db, settings=settings, profile=profile, llm=llm, read=read or default_read,
                 bank_loader=lambda: load_bank(tmp / "cv.yaml"))
    return p, llm, reads


def test_prepare_job_ready_with_cv_and_answers(env):
    tmp, db, *_, jobs = env
    p, llm, _ = preparer(env, [TAILORED, DRAFT])
    app = p.prepare_job(jobs[0].id, NOW)
    assert app.status == "ready" and app.form == FORM and app.ai_ban_quote is None
    kinds = {a.field_key: (a.kind, a.draft) for a in db.answers(app.id)}
    assert kinds == {"email": ("fact", "alex@example.com"), "why": ("open", "I built a RAG assistant."), "cv": ("cv", "CV")}
    assert db.latest_tailored_cv(jobs[0].id) is not None and len(list((tmp / "cvs").glob("*.pdf"))) == 1
    assert len(llm.calls) == 2


def test_existing_tailored_cv_is_reused(env):
    _, db, *_, jobs = env
    p, llm, _ = preparer(env, [TAILORED, DRAFT, DRAFT])
    p.prepare_job(jobs[0].id, NOW)
    db.conn.execute("DELETE FROM prepared_answers"); db.conn.execute("DELETE FROM prepared_apps"); db.conn.commit()
    p.prepare_job(jobs[0].id, NOW)
    assert len(llm.calls) == 3  # no second tailoring


def test_prepare_job_needs_you_records_reason(env):
    _, db, *_, jobs = env
    p, llm, _ = preparer(env, [], read=lambda job: NeedsYou(reason="Sign-in or account creation required"))
    app = p.prepare_job(jobs[0].id, NOW)
    assert (app.status, app.reason) == ("needs_you", "Sign-in or account creation required") and llm.calls == []


def test_prepare_twice_no_second_call(env):
    _, db, *_, jobs = env
    p, llm, reads = preparer(env, [TAILORED, DRAFT])
    first = p.prepare_job(jobs[0].id, NOW)
    second = p.prepare_job(jobs[0].id, NOW)
    assert first.id == second.id and reads == [jobs[0].id] and len(llm.calls) == 2


def test_needs_you_is_reread(env):
    _, db, *_, jobs = env
    results = [NeedsYou(reason="No application form found"), FORM]
    p, llm, _ = preparer(env, [TAILORED, DRAFT], read=lambda job: results.pop(0))
    assert p.prepare_job(jobs[0].id, NOW).status == "needs_you"
    app = p.prepare_job(jobs[0].id, NOW)
    assert app.status == "ready" and app.reason is None and len(db.answers(app.id)) == 3


def test_ai_ban_quote_stored(env):
    _, db, *_, jobs = env
    banned = FORM.model_copy(update={"page_text": "Join us. Please do not use AI to write your answers. Thanks."})
    p, llm, _ = preparer(env, [TAILORED, DRAFT], read=lambda job: banned)
    app = p.prepare_job(jobs[0].id, NOW)
    assert app.ai_ban_quote == "Please do not use AI to write your answers."
    assert "do not write" in llm.calls[1][0].lower()


def test_claude_error_keeps_facts_and_cv(env):
    _, db, *_, jobs = env
    p, _, _ = preparer(env, [TAILORED, LLMError("timeout"), ])
    app = p.prepare_job(jobs[0].id, NOW)
    by = {a.field_key: a for a in db.answers(app.id)}
    assert app.status == "ready"
    assert (by["email"].kind, by["email"].draft) == ("fact", "alex@example.com")
    assert by["cv"].kind == "cv" and by["why"].draft == "" and "Claude unavailable" in by["why"].problems[0]


def test_claude_unavailable_marks_needs_you_and_raises(env):
    _, db, *_, jobs = env
    p, _, _ = preparer(env, [LLMUnavailable("logged out")])
    with pytest.raises(LLMUnavailable):
        p.prepare_job(jobs[0].id, NOW)
    app = db.prepared_for_job(jobs[0].id)
    assert app.status == "needs_you" and "Claude unavailable" in app.reason


def test_ingest_appends_step_and_drafts(env):
    _, db, *_, jobs = env
    p, llm, _ = preparer(env, [TAILORED, DRAFT, {"answers": [{"key": "years", "kind": "open", "answer": "I built a RAG assistant."}]}])
    app = p.prepare_job(jobs[0].id, NOW)
    step2 = FormSnapshot(url="https://jobs/1/step2", page_text="Step 2",
                         fields=[FormField(key="years", selector="#years", label="Tell us more", kind="textarea")])
    after = p.ingest(app.id, step2, NOW)
    assert after.status == "ready" and [f.step for f in after.form.fields] == [0, 0, 0, 1]
    new = [a for a in db.answers(app.id) if a.step == 1]
    assert [a.field_key for a in new] == ["years"] and len(db.answers(app.id)) == 4
    assert '"years"' in llm.calls[2][0] and '"email"' not in llm.calls[2][0]  # only the new step is drafted


def test_ingest_into_needs_you_app_starts_at_step_zero(env):
    _, db, *_, jobs = env
    p, _, _ = preparer(env, [TAILORED, DRAFT], read=lambda job: NeedsYou(reason="Sign-in or account creation required"))
    app = p.prepare_job(jobs[0].id, NOW)
    after = p.ingest(app.id, FORM, NOW)
    assert after.status == "ready" and after.reason is None and {f.step for f in after.form.fields} == {0}


def test_prepare_pending_respects_cap_and_continues_after_failure(env):
    _, db, settings, *_, jobs = env
    settings.prepare.max_per_run = 2

    def read(job):
        if job.id == jobs[0].id:
            raise RuntimeError("browser crashed")
        return FORM

    p, _, _ = preparer(env, [TAILORED, DRAFT], read=read)
    assert p.prepare_pending(NOW) == 1
    assert db.prepared_for_job(jobs[1].id).status == "ready"
    assert db.prepared_for_job(jobs[2].id) is None  # score 50 < 75


def test_prepare_pending_stops_when_claude_unavailable(env):
    _, db, settings, *_, jobs = env
    p, _, reads = preparer(env, [LLMUnavailable("logged out")])
    assert p.prepare_pending(NOW) == 0 and reads == [jobs[0].id]


def test_ai_policy_field_sets_quote(env):
    _, db, *_, jobs = env
    policy = FormField(key="p", selector="#p", label="AI Policy for Application", kind="select", options=["Yes", "No"])
    form = FORM.model_copy(update={"fields": FIELDS + [policy]})
    p, llm, _ = preparer(env, [TAILORED, DRAFT], read=lambda job: form)
    app = p.prepare_job(jobs[0].id, NOW)
    assert app.ai_ban_quote.startswith("AI Policy for Application") and "do not write" in llm.calls[1][0].lower()
