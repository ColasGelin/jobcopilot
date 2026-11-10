"""Saved evaluation runs, the promoted baseline, and the report that compares a run with it."""

from __future__ import annotations

from pathlib import Path

from jobcopilot.evals.models import EvalRun
from jobcopilot.evals.stats import paired_change

TASK_NAMES = {"scoring": "Match scoring", "decisions": "Agreement with your decisions", "cv": "Tailored CV"}
QUESTIONS = {
    "scoring": "Do clear fits score high and clear misfits low?",
    "decisions": "Are the jobs you applied to scored above the ones you skipped?",
    "cv": "Does the CV pass the fact guard on the first try?",
}


class EvalStore:
    def __init__(self, folder: Path):
        self.folder = folder
        self.runs = folder / "runs"

    def save(self, run: EvalRun) -> None:
        self.runs.mkdir(parents=True, exist_ok=True)
        (self.runs / f"{run.id}.json").write_text(run.model_dump_json(indent=1), encoding="utf-8")

    def load(self, run_id: str) -> EvalRun | None:
        path = self.runs / f"{Path(run_id).name}.json"
        return EvalRun.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def list(self) -> list[EvalRun]:
        """Every run, newest first."""
        if not self.runs.exists():
            return []
        runs = [EvalRun.model_validate_json(p.read_text(encoding="utf-8")) for p in self.runs.glob("*.json")]
        return sorted(runs, key=lambda r: r.started_at, reverse=True)

    def latest(self) -> EvalRun | None:
        runs = self.list()
        return runs[0] if runs else None

    def baseline(self) -> EvalRun | None:
        marker = self.folder / "baseline"
        return self.load(marker.read_text(encoding="utf-8").strip()) if marker.exists() else None

    def promote(self, run_id: str) -> None:
        if self.load(run_id) is None:
            raise KeyError(run_id)
        (self.folder / "baseline").write_text(Path(run_id).name, encoding="utf-8")


def report(run: EvalRun, baseline: EvalRun | None) -> dict:
    """What the Evals page and the CLI show: one verdict, one row per task, the failing cases, the next action."""
    base_tasks = {t.task: t for t in baseline.tasks} if baseline else {}
    is_baseline = baseline is not None and baseline.id == run.id
    rows, failures = [], []
    for t in run.tasks:
        row = {"task": t.task, "name": TASK_NAMES.get(t.task, t.task), "question": QUESTIONS.get(t.task, ""), "metric": t.metric,
               "value": t.value, "ci_low": t.ci_low, "ci_high": t.ci_high, "n": t.n, "calls": t.calls, "seconds": t.seconds,
               "error": t.error, "model": run.models.get(t.task, ""), "prompt": run.prompts.get(t.task, ""),
               "change": None, "verdict": "new", "prompt_changed": False}
        base = base_tasks.get(t.task)
        if base is not None and t.cases and base.cases:
            change, _, _, verdict = paired_change({c.case_id: c.score for c in t.cases}, {c.case_id: c.score for c in base.cases})
            row.update(change=None if verdict == "new" else change, verdict=verdict,
                       prompt_changed=baseline.prompts.get(t.task) != run.prompts.get(t.task))
        rows.append(row)
        failures += [{"task": row["name"], "case": c} for c in t.cases if not c.passed]
    worse = [r["name"] for r in rows if r["verdict"] == "worse"]
    better = [r["name"] for r in rows if r["verdict"] == "better"]
    if baseline is None:
        verdict, action = "no_baseline", "No baseline yet. If these results look right, promote this run to be the baseline."
    elif worse:
        verdict = "blocked"
        action = f"Got worse: {', '.join(worse).lower()}. Review the failing cases below before keeping this change."
    elif is_baseline:
        verdict, action = "safe", "This run is the baseline. Run again after you change a prompt or a model."
    elif better:
        verdict, action = "safe", f"Got better: {', '.join(better).lower()}. Promote this run to be the new baseline."
    else:
        verdict, action = "safe", "No real change from the baseline. Nothing to do."
    return {"verdict": verdict, "next": action, "rows": rows, "failures": failures, "is_baseline": is_baseline,
            "run": run, "baseline": baseline}
