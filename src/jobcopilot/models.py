"""Data models shared by every pipeline stage."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field

STATUSES = ("new", "shortlisted", "applied", "interview", "offer", "rejected", "skipped")


class Job(BaseModel):
    url: str
    source: str
    company: str
    title: str
    location: str = ""
    remote: bool = False
    salary_text: str | None = None
    description: str = ""
    posted_at: datetime | None = None

    @property
    def id(self) -> str:
        return hashlib.sha1(self.url.encode()).hexdigest()[:12]


class Evaluation(BaseModel):
    job_id: str
    score: int = Field(ge=0, le=100)
    fit_reasons: str
    gaps: str
    model: str
    prompt_version: str


class Draft(BaseModel):
    job_id: str
    cv_tweaks: list[str]
    cover_note: str
    outreach_message: str
    contact_hint: str
    model: str
    prompt_version: str


class Run(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    fetched: int = 0
    new: int = 0
    kept: int = 0
    scored: int = 0
    drafted: int = 0
    failed_sources: list[str] = Field(default_factory=list)
    error: str | None = None
