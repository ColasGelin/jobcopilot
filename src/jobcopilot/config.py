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


class ApplicationProfile(BaseModel):
    """Facts copied into application forms as written. Every key is optional."""

    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None  # defaults to "first last"
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    country: str | None = None
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    salary_expectation: str | None = None
    available_from: str | None = None
    notice_period: str | None = None
    work_authorization: str | None = None
    willing_to_relocate: str | None = None

    def values(self) -> dict[str, str]:
        data = self.model_dump()
        if not data["full_name"] and data["first_name"] and data["last_name"]:
            data["full_name"] = f"{data['first_name']} {data['last_name']}"
        return {k: v for k, v in data.items() if v and str(v).strip()}


class Profile(BaseModel):
    hard_filters: HardFilters
    summary: str
    application: ApplicationProfile = Field(default_factory=ApplicationProfile)


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


class BoardSearch(BaseModel):
    """One search on a job board read through JobSpy."""

    term: str
    location: str
    remote: bool = False  # ask the board for remote jobs only; it doesn't always say so on the listing
    country: str | None = None  # Indeed only: the country's Indeed site to search (e.g. france); default indeed_country


class CompanyToCheck(BaseModel):
    """A company worth checking by hand (its job board can't be read automatically)."""

    name: str
    url: str | None = None  # leave out when there's no known careers page
    why: str = ""
    location: str = ""
    category: str = "Other"


class PrepareSettings(BaseModel):
    """Applications prepared automatically after each run (form read, CV tailored, answers drafted)."""

    enabled: bool = True
    min_score: int = 75
    max_per_run: int = 10


class MailSettings(BaseModel):
    """The inbox read every hour to move applications forward. The password comes from JOBCOPILOT_IMAP_PASSWORD."""

    enabled: bool = False
    host: str = "imap.gmail.com"
    user: str = ""
    folder: str = "INBOX"
    lookback_days: int = 7
    check_every_minutes: int = 60


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    top_n: int = 10
    min_score: int = 60
    lookback_days: int = 14
    sources: list[str] = Field(
        default_factory=lambda: ["remotive", "remoteok", "arbeitnow", "hn", "jobicy", "himalayas", "workingnomads", "wwr", "watchlist"]
    )
    himalayas_queries: list[str] = Field(default_factory=lambda: ["python", "AI engineer", "LLM", "machine learning"])
    himalayas_countries: list[str] = Field(default_factory=lambda: ["Spain"])
    watchlist: list[WatchlistEntry] = Field(default_factory=list)
    companies_to_check: list[CompanyToCheck] = Field(default_factory=list)
    # Caps LLM usage when a source (e.g. a new watchlist) brings many jobs at once; the rest wait for the next run.
    max_scored_per_run: int = 150
    prepare: PrepareSettings = Field(default_factory=PrepareSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
    # Used only when "linkedin" is in `sources` (off by default: it goes against LinkedIn's terms).
    linkedin_searches: list[BoardSearch] = Field(default_factory=lambda: [
        BoardSearch(term="AI engineer", location="Málaga, Spain"),
        BoardSearch(term="Python developer", location="Málaga, Spain"),
        BoardSearch(term="LLM engineer", location="Spain", remote=True),
    ])
    linkedin_results: int = 25  # per search
    linkedin_hours_old: int = 48
    # Used only when "indeed" is in `sources` (off by default: scraping goes against Indeed's terms too).
    indeed_searches: list[BoardSearch] = Field(default_factory=lambda: [
        BoardSearch(term="AI engineer", location="Spain", remote=True),
        BoardSearch(term="Python developer", location="Málaga, Spain"),
    ])
    indeed_results: int = 25  # per search
    indeed_hours_old: int = 72
    indeed_country: str = "spain"
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

    @property
    def cv_path(self) -> Path:
        return self.data_dir / "cv.yaml"

    @property
    def cvs_dir(self) -> Path:
        return self.data_dir / "cvs"

    @property
    def voice_dir(self) -> Path:
        return self.data_dir / "voice"


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_profile(path: Path) -> Profile:
    return Profile.model_validate(_read_yaml(path))


def load_settings(path: Path) -> Settings:
    data = _read_yaml(path) if path.exists() else {}
    data.setdefault("data_dir", path.parent)
    return Settings.model_validate(data)
