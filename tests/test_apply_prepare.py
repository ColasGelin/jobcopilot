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


def test_unexpected_error_marks_needs_you(env):
    _, db, *_, jobs = env
    p, _, _ = preparer(env, [], read=lambda job: (_ for _ in ()).throw(KeyError("questions")))
    app = p.prepare_job(jobs[0].id, NOW)
    assert app.status == "needs_you" and "Preparing failed" in app.reason


def test_stale_reading_and_claude_unavailable_are_retried(env):
    from datetime import timedelta
    _, db, settings, *_, jobs = env
    stuck = db.create_prepared(jobs[0].id, "reading", NOW - timedelta(minutes=30))
    db.create_prepared(jobs[1].id, "needs_you", NOW, reason="Claude unavailable: logged out")
    assert db.jobs_to_prepare(75, 10, now=NOW) == [jobs[0].id, jobs[1].id]
    p, _, reads = preparer(env, [TAILORED, DRAFT])
    assert p.prepare_job(jobs[0].id, NOW).status == "ready" and reads == [jobs[0].id]
    fresh = db.create_prepared(jobs[2].id, "drafting", NOW)
    assert p.prepare_job(jobs[2].id, NOW).status == "drafting"  # still being prepared: left alone


def test_reread_replaces_answers_instead_of_duplicating(env):
    _, db, *_, jobs = env
    results = [FORM, NeedsYou(reason="x")]
    p, _, _ = preparer(env, [TAILORED, DRAFT, DRAFT], read=lambda job: FORM)
    app = p.prepare_job(jobs[0].id, NOW)
    db.update_prepared(app.id, NOW, status="needs_you", reason="Claude unavailable: x")
    p.prepare_job(jobs[0].id, NOW)
    assert len(db.answers(app.id)) == 3


def test_same_step_ingested_twice_replaces_it(env):
    _, db, *_, jobs = env
    step = FormSnapshot(url="https://jobs/1/2", fields=[FormField(key="more", selector="#more", label="More", kind="textarea")])
    resp = {"answers": [{"key": "more", "kind": "open", "answer": "I built a RAG assistant."}]}
    p, llm, _ = preparer(env, [TAILORED, DRAFT, resp, resp])
    app = p.prepare_job(jobs[0].id, NOW)
    p.ingest(app.id, step, NOW)
    after = p.ingest(app.id, step, NOW)  # Continue clicked twice on the same page
    assert [f.step for f in after.form.fields] == [0, 0, 0, 1] and len(db.answers(app.id)) == 4


def test_ingest_claude_down_keeps_ready_app(env):
    _, db, *_, jobs = env
    step = FormSnapshot(url="https://jobs/1/2", fields=[FormField(key="more", selector="#more", label="More", kind="textarea")])
    p, _, _ = preparer(env, [TAILORED, DRAFT, LLMUnavailable("logged out")])
    app = p.prepare_job(jobs[0].id, NOW)
    with pytest.raises(LLMUnavailable):
        p.ingest(app.id, step, NOW)
    after = db.get_prepared(app.id)
    assert after.status == "ready" and len(after.form.fields) == 3 and len(db.answers(app.id)) == 3


def test_add_job_by_link_scores_and_prepares(env):
    _, db, *_ = env
    job = make_job(9, company="Linked", url="https://acme.com/careers/9")
    score = {"results": [{"job_id": job.id, "score": 55, "fit_reasons": "ok", "gaps": "none"}]}
    p, llm, reads = preparer(env, [score, TAILORED, DRAFT])
    p.fetch = lambda url: job
    job_id, error = p.add_job("https://acme.com/careers/9", NOW)
    assert (job_id, error) == (job.id, None)
    assert db.get_job(job.id).company == "Linked" and db.match(job.id).evaluation.score == 55
    assert db.prepared_for_job(job.id).status == "ready" and reads == [job.id]  # prepared even below min_score


def test_add_job_known_link_reuses_it(env):
    _, db, *_, jobs = env
    p, llm, _ = preparer(env, [TAILORED, DRAFT])
    p.fetch = lambda url: pytest.fail("known link must not be fetched again")
    assert p.add_job(jobs[0].url, NOW) == (jobs[0].id, None)


def test_add_job_fetch_problem_reported(env):
    _, db, *_ = env
    p, llm, _ = preparer(env, [])
    p.fetch = lambda url: NeedsYou(reason="Page failed to load: 404")
    assert p.add_job("https://acme.com/x", NOW) == (None, "Page failed to load: 404")


def test_add_job_scoring_failure_still_prepares(env):
    _, db, *_ = env
    job = make_job(9, company="Linked", url="https://acme.com/careers/9")
    p, llm, _ = preparer(env, [LLMError("x"), LLMError("x"), TAILORED, DRAFT])  # scoring retries once
    p.fetch = lambda url: job
    job_id, error = p.add_job(job.url, NOW)
    assert error is None and db.prepared_for_job(job_id).status == "ready"
