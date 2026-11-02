"""Load the candidate profile (profile.yaml) and app settings (config.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class HardFilters(BaseModel):
    remote_regions: list[str] = Field(default_factory=list)
    onsite_cities: list[str] = Field(default_factory=list)
    exclude_title_keywords: list[str] = Field(default_factory=list)
    exclude_text_patterns: list[str] = Field(default_factory=list)
    min_salary_eur: int | None = None


class Profile(BaseModel):
    hard_filters: HardFilters
    summary: str


class LLMSettings(BaseModel):
    backend: Literal["claude_code"] = "claude_code"
    score_model: str = "haiku"
    draft_model: str = "sonnet"
    timeout_s: int = 300


class WatchlistEntry(BaseModel):
    """A company whose public job board is checked every run."""

    name: str
    ats: Literal["greenhouse", "ashby", "lever"]
    slug: str


class CompanyToCheck(BaseModel):
    """A company worth checking by hand (its job board can't be read automatically)."""

    name: str
    url: str | None = None  # leave out when there's no known careers page
    why: str = ""
    location: str = ""
    category: str = "Other"


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    top_n: int = 10
    min_score: int = 60
    lookback_days: int = 14
    sources: list[str] = Field(
        default_factory=lambda: ["remotive", "remoteok", "arbeitnow", "hn", "jobicy", "himalayas", "workingnomads", "wwr", "watchlist"]
    )
    himalayas_queries: list[str] = Field(default_factory=lambda: ["python", "AI engineer", "LLM", "machine learning"])
    himalayas_country: str = "Spain"
    watchlist: list[WatchlistEntry] = Field(default_factory=list)
    companies_to_check: list[CompanyToCheck] = Field(default_factory=list)
    # Caps LLM usage when a source (e.g. a new watchlist) brings many jobs at once; the rest wait for the next run.
    max_scored_per_run: int = 150
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_profile(path: Path) -> Profile:
    return Profile.model_validate(_read_yaml(path))


def load_settings(path: Path) -> Settings:
    data = _read_yaml(path) if path.exists() else {}
    data.setdefault("data_dir", path.parent)
    return Settings.model_validate(data)
