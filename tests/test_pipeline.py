from datetime import datetime, timezone

import pytest

from conftest import make_job
from jobcopilot.config import HardFilters, Profile, Settings
from jobcopilot.db import DB
from jobcopilot.llm import FakeLLMClient, LLMUnavailable
from jobcopilot.pipeline import run_pipeline

NOW = datetime(2026, 11, 2, 8, 30, tzinfo=timezone.utc)


class FakeSource:
    def __init__(self, name, jobs=None, error=None):
        self.name, self.jobs, self.error = name, jobs or [], error

    def fetch(self, since):
        if self.error:
            raise self.error
        return list(self.jobs)


class SmartFake(FakeLLMClient):
    """Scores every job in the prompt with `score`, and returns a valid draft."""

    def __init__(self, score=75):
        super().__init__([])
        self.score = score

    def complete_json(self, prompt, schema, model):
        self.calls.append((prompt, schema, model))
        if "results" in schema["properties"]:
            import re
            ids = re.findall(r'"job_id": "([0-9a-f]{12})"', prompt)
            return {"results": [{"job_id": i, "score": self.score, "fit_reasons": "ok", "gaps": "none"} for i in ids]}
        return {"cv_tweaks": ["a", "b"], "cover_note": "cover", "outreach_message": "hi", "contact_hint": "CTO"}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(data_dir=tmp_path, top_n=2)
    profile = Profile(hard_filters=HardFilters(remote_regions=["Europe"]), summary="me")
    return settings, profile, DB(":memory:")


def test_end_to_end_happy_path(setup):
    settings, profile, db = setup
    sources = [FakeSource("a", [make_job(1), make_job(2)]), FakeSource("b", [make_job(3), make_job(4, location="USA")])]
    llm = SmartFake()
    run, path = run_pipeline(settings, profile, db, sources, llm, NOW)
    assert (run.fetched, run.new, run.kept, run.scored, run.drafted) == (4, 4, 3, 3, 2)
    assert run.error is None and run.finished_at is not None
    assert path == settings.reports_dir / "2026-11-02.md" and path.exists()
    assert [c[2] for c in llm.calls] == ["haiku", "sonnet", "sonnet"]


def test_failing_source_is_recorded_and_others_continue(setup):
    settings, profile, db = setup
    sources = [FakeSource("broken", error=RuntimeError("boom")), FakeSource("ok", [make_job(1)])]
    run, path = run_pipeline(settings, profile, db, sources, SmartFake(), NOW)
    assert run.failed_sources == ["broken"] and run.kept == 1
    assert "Failed sources: broken" in path.read_text()


def test_second_run_same_day_adds_nothing(setup):
    settings, profile, db = setup
    sources = [FakeSource("a", [make_job(1)])]
    run_pipeline(settings, profile, db, sources, SmartFake(), NOW)
    llm = SmartFake()
    run, path = run_pipeline(settings, profile, db, sources, llm, NOW)
    assert run.new == 0 and llm.calls == []
    assert path.exists()


def test_llm_unavailable_aborts_with_error(setup):
    settings, profile, db = setup
    run, path = run_pipeline(
        settings, profile, db, [FakeSource("a", [make_job(1)])], FakeLLMClient([LLMUnavailable("Claude Code is logged out")]), NOW
    )
    assert path is None and "logged out" in run.error
    assert db.get_run(1).finished_at is not None


def test_low_scores_get_no_drafts(setup):
    settings, profile, db = setup
    llm = SmartFake(score=40)
    run, _ = run_pipeline(settings, profile, db, [FakeSource("a", [make_job(1), make_job(2)])], llm, NOW)
    assert run.drafted == 0 and [c[2] for c in llm.calls] == ["haiku"]


def test_draft_failure_keeps_item_and_marks_error(setup):
    settings, profile, db = setup

    class DraftFails(SmartFake):
        def complete_json(self, prompt, schema, model):
            if "results" not in schema["properties"]:
                self.calls.append((prompt, schema, model))
                return {"bad": True}
            return super().complete_json(prompt, schema, model)

    run, path = run_pipeline(settings, profile, db, [FakeSource("a", [make_job(1)])], DraftFails(), NOW)
    assert run.drafted == 0 and "Draft failed" in path.read_text()
    assert db.notes(make_job(1).id) == "llm_error"


def test_jobs_from_aborted_run_are_retried_next_run(setup):
    settings, profile, db = setup
    sources = [FakeSource("a", [make_job(1)])]
    run_pipeline(settings, profile, db, sources, FakeLLMClient([LLMUnavailable("logged out")]), NOW)
    run, path = run_pipeline(settings, profile, db, sources, SmartFake(), NOW)
    assert run.new == 1 and run.scored == 1 and run.drafted == 1


def test_rejected_jobs_are_not_stored_so_profile_fixes_apply(setup):
    settings, profile, db = setup
    run_pipeline(settings, profile, db, [FakeSource("a", [make_job(1, location="USA")])], SmartFake(), NOW)
    assert db.known_urls() == set()


def test_second_run_with_nothing_new_keeps_morning_report(setup):
    settings, profile, db = setup
    sources = [FakeSource("a", [make_job(1, company="MorningCo")])]
    _, path = run_pipeline(settings, profile, db, sources, SmartFake(), NOW)
    run_pipeline(settings, profile, db, sources, SmartFake(), NOW)
    assert "MorningCo" in path.read_text()


class AlwaysFails(FakeLLMClient):
    def __init__(self):
        super().__init__([])

    def complete_json(self, prompt, schema, model):
        self.calls.append((prompt, schema, model))
        from jobcopilot.llm import LLMError
        raise LLMError("You've hit your usage limit")


def test_all_batches_failing_aborts_and_keeps_jobs_for_next_run(setup):
    settings, profile, db = setup
    sources = [FakeSource("a", [make_job(1), make_job(2)])]
    run, path = run_pipeline(settings, profile, db, sources, AlwaysFails(), NOW)
    assert path is None and run.error and "usage limit" in run.error
    run2, _ = run_pipeline(settings, profile, db, sources, SmartFake(), NOW)
    assert run2.scored == 2


def test_partially_failed_scoring_is_retried_next_run_and_reported(setup):
    settings, profile, db = setup

    class FirstBatchFails(SmartFake):
        def complete_json(self, prompt, schema, model):
            if "results" in schema["properties"] and make_job(1).id in prompt:
                self.calls.append((prompt, schema, model))
                from jobcopilot.llm import LLMError
                raise LLMError("overloaded")
            return super().complete_json(prompt, schema, model)

    settings = settings.model_copy(update={"top_n": 5})
    jobs = [make_job(i) for i in range(1, 12)]  # 11 jobs → batches of 10 + 1
    run, path = run_pipeline(settings, profile, db, [FakeSource("a", jobs)], FirstBatchFails(), NOW)
    assert run.scored == 1
    assert "10 jobs could not be scored" in path.read_text()
    run2, _ = run_pipeline(settings, profile, db, [FakeSource("a", jobs)], SmartFake(), NOW)
    assert run2.new == 10 and run2.scored == 10


def test_source_partial_errors_are_recorded(setup):
    settings, profile, db = setup

    class PartlyBroken(FakeSource):
        errors = ["watchlist:Ghost"]

    run, _ = run_pipeline(settings, profile, db, [PartlyBroken("watchlist", [make_job(1)])], SmartFake(), NOW)
    assert run.failed_sources == ["watchlist:Ghost"] and run.kept == 1


def test_scoring_is_capped_and_rest_wait_for_next_run(setup):
    settings, profile, db = setup
    settings = settings.model_copy(update={"max_scored_per_run": 3, "top_n": 0})
    jobs = [make_job(i) for i in range(1, 6)]
    run, _ = run_pipeline(settings, profile, db, [FakeSource("a", jobs)], SmartFake(), NOW)
    assert run.kept == 5 and run.scored == 3
    run2, _ = run_pipeline(settings, profile, db, [FakeSource("a", jobs)], SmartFake(), NOW)
    assert run2.new == 2 and run2.scored == 2
