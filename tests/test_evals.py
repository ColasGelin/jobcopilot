import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import make_job
from jobcopilot.config import HardFilters, Profile, Settings
from jobcopilot.db import DB
from jobcopilot.evals.models import Case, CaseResult, EvalRun, TaskResult
from jobcopilot.evals.runner import run_evals
from jobcopilot.evals.stats import mean_ci, paired_change
from jobcopilot.evals.store import EvalStore, report
from jobcopilot.evals.tasks import decision_cases, load_cases
from jobcopilot.llm import FakeLLMClient, LLMError
from jobcopilot.models import Evaluation

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"
TAILORED = {
    "language": "en", "headline": "AI Engineer", "summary": "AI engineer building RAG pipelines.",
    "experience": [{"id": "acme", "bullets": [{"source_id": "acme.rag", "text": "Built a RAG assistant."}]},
                   {"id": "beta", "bullets": [{"source_id": "beta.api", "text": "Built FastAPI services."}]}],
    "projects": [], "skills": [], "extras": [],
}


def case(i, expect, tags=()):
    return Case(id=f"c{i}", task="scoring", tags=list(tags), expect=expect,
                job={"company": f"Co{i}", "title": "AI Engineer", "location": "Spain", "remote": True, "description": "Build agents."})


def scores(cases, values):
    return {"results": [{"job_id": c.to_job().id, "score": v, "fit_reasons": "fit", "gaps": "gap"} for c, v in zip(cases, values)]}


@pytest.fixture
def env(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "cv.yaml")
    return {"profile": Profile(hard_filters=HardFilters(), summary="Junior AI engineer."), "settings": Settings(data_dir=tmp_path),
            "db": DB(tmp_path / "jobs.db"), "now": NOW}


# --- statistics ---------------------------------------------------------------
def test_mean_ci_brackets_the_mean_and_is_repeatable():
    mean, low, high = mean_ci([1, 1, 1, 0, 0, 1, 1, 0, 1, 1])
    assert mean == 0.7 and low < 0.7 < high and 0 <= low and high <= 1
    assert mean_ci([1, 1, 1, 0, 0, 1, 1, 0, 1, 1]) == (mean, low, high)
    assert mean_ci([1.0]) == (1.0, 1.0, 1.0) and mean_ci([]) == (0.0, 0.0, 0.0)


def test_paired_change_needs_a_clear_drop_to_say_worse():
    base = {f"c{i}": 1.0 for i in range(20)}
    assert paired_change({**base, "c0": 0.0}, base)[3] == "same"          # one case out of 20 is noise
    assert paired_change({k: 0.0 if int(k[1:]) < 12 else 1.0 for k in base}, base)[3] == "worse"
    assert paired_change(base, {k: 0.0 if int(k[1:]) < 12 else 1.0 for k in base})[3] == "better"
    assert paired_change({"x": 1.0}, {"y": 1.0})[3] == "new"                # no case in common


# --- tasks ----------------------------------------------------------------------
def test_packaged_scoring_cases_are_valid():
    cases = load_cases(None)
    assert len(cases) >= 20 and len({c.id for c in cases}) == len(cases)
    assert all(("min" in c.expect) != ("max" in c.expect) for c in cases)
    assert sum("min" in c.expect for c in cases) >= 8 and sum("max" in c.expect for c in cases) >= 8
    assert sum("cv" in c.tags for c in cases) >= 3


def test_cases_in_the_data_folder_replace_the_packaged_ones(tmp_path):
    (tmp_path / "scoring.jsonl").write_text(case(1, {"min": 70}).model_dump_json() + "\n\n")
    assert [c.id for c in load_cases(tmp_path)] == ["c1"]


def test_scoring_task_checks_each_score_against_its_band(env):
    cases = [case(1, {"min": 70}), case(2, {"min": 70}), case(3, {"max": 45}), case(4, {"max": 45})]
    llm = FakeLLMClient([scores(cases, [85, 60, 20, 50])])
    run = run_evals(["scoring"], llm=llm, cases=cases, **env)
    task = run.tasks[0]
    assert (task.task, task.metric, task.value, task.n, task.calls) == ("scoring", "in_band", 0.5, 4, 1)
    assert [c.passed for c in task.cases] == [True, False, True, False]
    assert task.cases[1].expected == "70 or more" and task.cases[1].actual == "60" and task.cases[1].note == "gap"
    assert run.prompts["scoring"] == "score_v1" and run.models["scoring"] == "haiku"


def test_a_case_the_model_did_not_score_fails(env):
    cases = [case(1, {"min": 70}), case(2, {"min": 70})]
    run = run_evals(["scoring"], llm=FakeLLMClient([scores(cases[:1], [90])]), cases=cases, **env)
    assert run.tasks[0].value == 0.5 and run.tasks[0].cases[1].actual == "not scored"


def test_limit_takes_fits_and_misfits_alike(env):
    cases = [case(i, {"min": 70}) for i in range(4)] + [case(i, {"max": 45}) for i in range(4, 8)]
    llm = FakeLLMClient([{"results": []}])
    run = run_evals(["scoring"], llm=llm, cases=cases, limit=4, **env)
    assert [c.case_id for c in run.tasks[0].cases] == ["c0", "c4", "c1", "c5"]


def test_decisions_task_ranks_applied_jobs_above_skipped_ones(env):
    db = env["db"]
    jobs = [make_job(i, company=f"Co{i}") for i in range(4)]
    db.insert_jobs(jobs, NOW)
    for j, status in zip(jobs, ["applied", "interview", "skipped", "skipped"]):
        db.set_status(j.id, status, NOW)
    cases = decision_cases(db)
    assert sorted(c.expect["applied"] for c in cases) == [False, False, True, True] and all(c.source == "decision" for c in cases)
    result = {"results": [{"job_id": j.id, "score": s, "fit_reasons": "", "gaps": ""} for j, s in zip(jobs, [90, 40, 50, 10])]}
    task = run_evals(["decisions"], llm=FakeLLMClient([result]), **env).tasks[0]
    # Co0 beats both skipped (1.0), Co1 beats one (0.5); Co2 is beaten by one applied (0.5), Co3 by both (1.0)
    assert task.metric == "ranks_applied_higher" and task.value == 0.75
    assert {c.label.split(" ")[0]: c.score for c in task.cases} == {"Co0": 1.0, "Co1": 0.5, "Co2": 0.5, "Co3": 1.0}


def test_decisions_task_needs_both_kinds_of_decision(env):
    job = make_job(1)
    env["db"].insert_jobs([job], NOW)
    env["db"].set_status(job.id, "applied", NOW)
    task = run_evals(["decisions"], llm=FakeLLMClient([]), **env).tasks[0]
    assert task.n == 0 and "at least one applied and one skipped" in task.error


def test_cv_task_counts_cvs_that_pass_the_fact_guard_first_try(env):
    cases = [case(1, {"min": 70}, tags=["cv"]), case(2, {"min": 70}, tags=["cv"]), case(3, {"min": 70})]
    invented = {**TAILORED, "summary": "AI engineer with 12 years of Rust."}
    task = run_evals(["cv"], llm=FakeLLMClient([TAILORED, invented]), cases=cases, **env).tasks[0]
    assert (task.metric, task.value, task.n, task.calls) == ("clean_first_try", 0.5, 2, 2)
    assert task.cases[1].passed is False and "12" in task.cases[1].actual


def test_a_failing_call_fails_the_case_not_the_run(env):
    cases = [case(1, {"min": 70}, tags=["cv"])]
    task = run_evals(["cv"], llm=FakeLLMClient([LLMError("timeout")]), cases=cases, **env).tasks[0]
    assert task.value == 0.0 and "timeout" in task.cases[0].actual


def test_unknown_task_is_refused(env):
    with pytest.raises(ValueError, match="unknown task"):
        run_evals(["nope"], llm=FakeLLMClient([]), **env)


# --- store and report -----------------------------------------------------------
def run_with(run_id, values, task="scoring", started=NOW):
    cases = [CaseResult(case_id=f"c{i}", label=f"Co{i}", score=v, passed=v >= 0.5, expected="70 or more", actual="x") for i, v in enumerate(values)]
    mean = sum(values) / len(values)
    return EvalRun(id=run_id, started_at=started, models={task: "haiku"}, prompts={task: "score_v1"},
                   tasks=[TaskResult(task=task, metric="in_band", value=mean, ci_low=mean, ci_high=mean, n=len(values), calls=1, seconds=2.0, cases=cases)])


def test_store_keeps_runs_newest_first_and_a_baseline(tmp_path):
    store = EvalStore(tmp_path / "evals")
    assert store.latest() is None and store.baseline() is None and store.list() == []
    store.save(run_with("a", [1, 1], started=NOW - timedelta(days=1)))
    store.save(run_with("b", [1, 0]))
    assert [r.id for r in store.list()] == ["b", "a"] and store.latest().id == "b"
    store.promote("a")
    assert store.baseline().id == "a"
    with pytest.raises(KeyError):
        store.promote("missing")


def test_report_without_a_baseline_asks_to_promote():
    r = report(run_with("a", [1, 1, 0]), None)
    assert r["verdict"] == "no_baseline" and "baseline" in r["next"].lower()
    assert r["rows"][0]["change"] is None and len(r["failures"]) == 1


def test_report_blocks_on_a_clear_drop_and_passes_on_noise():
    base = run_with("a", [1] * 20)
    worse = report(run_with("b", [0] * 12 + [1] * 8), base)
    assert worse["verdict"] == "blocked" and worse["rows"][0]["verdict"] == "worse" and worse["rows"][0]["change"] == pytest.approx(-0.6)
    assert "scoring" in worse["next"]
    noise = report(run_with("c", [0] + [1] * 19), base)
    assert noise["verdict"] == "safe" and noise["rows"][0]["verdict"] == "same"
    better = report(run_with("d", [1] * 20), run_with("e", [0] * 12 + [1] * 8))
    assert better["verdict"] == "safe" and "Promote" in better["next"]


def test_report_of_the_baseline_itself_is_safe():
    base = run_with("a", [1, 1, 0])
    r = report(base, base)
    assert r["verdict"] == "safe" and r["is_baseline"]
