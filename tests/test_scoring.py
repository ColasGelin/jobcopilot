import pytest

from conftest import make_job
from jobcopilot.llm import FakeLLMClient, LLMError, LLMUnavailable
from jobcopilot.scoring import SCORE_PROMPT_VERSION, score_jobs


def result(job, score=70):
    return {"job_id": job.id, "score": score, "fit_reasons": "Python + RAG", "gaps": "No Go"}


def test_scores_in_batches(profile):
    jobs = [make_job(i) for i in range(23)]
    batches = [jobs[0:10], jobs[10:20], jobs[20:23]]
    fake = FakeLLMClient([{"results": [result(j) for j in b]} for b in batches])
    evals, failed = score_jobs(jobs, profile, fake, "haiku", batch_size=10)
    assert len(fake.calls) == 3
    assert len(evals) == 23 and failed == []
    assert evals[0].prompt_version == SCORE_PROMPT_VERSION == "score_v1"
    assert evals[0].model == "haiku"


def test_prompt_contains_profile_and_job(profile):
    job = make_job(1, company="Acme Robotics")
    fake = FakeLLMClient([{"results": [result(job)]}])
    score_jobs([job], profile, fake, "haiku")
    prompt = fake.calls[0][0]
    assert "Junior AI engineer" in prompt and "Acme Robotics" in prompt and job.id in prompt


def test_retries_once_on_invalid_then_succeeds(profile):
    job = make_job(1)
    fake = FakeLLMClient([{"results": [result(job, score=150)]}, {"results": [result(job)]}])
    evals, failed = score_jobs([job], profile, fake, "haiku")
    assert len(evals) == 1 and failed == []
    assert "previous answer was invalid" in fake.calls[1][0]


def test_batch_fails_after_two_errors(profile):
    jobs = [make_job(1), make_job(2)]
    evals, failed = score_jobs(jobs, profile, FakeLLMClient([LLMError("a"), LLMError("b")]), "haiku")
    assert evals == [] and sorted(failed) == sorted(j.id for j in jobs)


def test_missing_job_in_response_is_failed(profile):
    jobs = [make_job(1), make_job(2)]
    evals, failed = score_jobs(jobs, profile, FakeLLMClient([{"results": [result(jobs[0])]}]), "haiku")
    assert [e.job_id for e in evals] == [jobs[0].id] and failed == [jobs[1].id]


def test_unknown_job_ids_are_ignored(profile):
    job = make_job(1)
    fake = FakeLLMClient([{"results": [result(job), {**result(job), "job_id": "zzz"}]}])
    evals, _ = score_jobs([job], profile, fake, "haiku")
    assert [e.job_id for e in evals] == [job.id]


def test_unavailable_propagates(profile):
    with pytest.raises(LLMUnavailable):
        score_jobs([make_job(1)], profile, FakeLLMClient([LLMUnavailable("logged out")]), "haiku")
