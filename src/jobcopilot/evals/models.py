from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jobcopilot.models import Job


class Case(BaseModel):
    """One test case: a job posting and what is expected of the AI on it."""

    id: str
    task: str = "scoring"
    tags: list[str] = Field(default_factory=list)
    source: str = "authored"  # where the case comes from: authored, or a decision the candidate made
    job: dict
    expect: dict = Field(default_factory=dict)

    def to_job(self) -> Job:
        return Job(**({"url": f"https://eval.example/{self.id}", "source": "eval"} | self.job))

    @property
    def label(self) -> str:
        return f"{self.job.get('company', '')} — {self.job.get('title', '')}"


class CaseResult(BaseModel):
    case_id: str
    label: str
    score: float  # 0 to 1; the task's metric is the mean over its cases
    passed: bool
    expected: str
    actual: str
    note: str = ""


class TaskResult(BaseModel):
    task: str
    metric: str
    value: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    n: int = 0
    calls: int = 0  # LLM calls made
    seconds: float = 0.0  # time spent in them
    cases: list[CaseResult] = Field(default_factory=list)
    error: str | None = None


class EvalRun(BaseModel):
    id: str
    started_at: datetime
    models: dict[str, str] = Field(default_factory=dict)
    prompts: dict[str, str] = Field(default_factory=dict)
    tasks: list[TaskResult] = Field(default_factory=list)
