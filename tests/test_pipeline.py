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
