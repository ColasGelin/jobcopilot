"""The experience bank: every true fact that may appear on a CV (data/cv.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, model_validator


class BankError(Exception):
    """cv.yaml is missing or invalid; the message is meant for the user."""


class Bullet(BaseModel):
    id: str
    text: str
    tags: list[str] = []
    main: bool = False


class Experience(BaseModel):
    id: str
    title: str
    company: str
    location: str = ""
    dates: str
    bullets: list[Bullet]


class Project(BaseModel):
    id: str
    section: Literal["engineering", "hackathons"]
    name: str
    status: Literal["done", "in_progress"] = "done"
    text: str
    tags: list[str] = []
    main: bool = False


class SkillGroup(BaseModel):
    group: str
    items: list[str]
    main: bool = True                    # shown on the main CV
    main_items: list[str] | None = None  # shorter list for the main CV; all items stay available to tailoring

    @model_validator(mode="after")
    def _main_items_known(self) -> SkillGroup:
        unknown = [i for i in self.main_items or [] if i not in self.items]
        if unknown:
            raise ValueError(f"main_items {unknown} are not in items of {self.group!r}")
        return self


class Education(BaseModel):
    id: str
    school: str
    degree: str
    place: str = ""
    dates: str = ""
    main: bool = True


class Certification(BaseModel):
    id: str
    name: str
    detail: str = ""
    date: str = ""
    main: bool = True


class Language(BaseModel):
    name: str
    level: str


class Extra(BaseModel):
    id: str
    section: Literal["other", "interests"]
    text: str
    tags: list[str] = []
    main: bool = False


class Contact(BaseModel):
    name: str
    location: str
    email: str
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""


class Bank(BaseModel):
    contact: Contact
    headlines: list[str]
    main_headline: str
    summary: str
    experience: list[Experience]
    projects: list[Project] = []
    skills: list[SkillGroup] = []
    education: list[Education] = []
    certifications: list[Certification] = []
    languages: list[Language] = []
    extras: list[Extra] = []
    # Per-language overrides of section titles, e.g. {en: {engineering: "Engineering · 42 School"}}.
    section_titles: dict[Literal["en", "es", "fr"], dict[str, str]] = {}

    @model_validator(mode="after")
    def _check(self) -> Bank:
        seen: set[str] = set()
        ids = [e.id for e in self.experience]
        ids += [b.id for e in self.experience for b in e.bullets]
        ids += [x.id for x in (*self.projects, *self.extras, *self.education, *self.certifications)]
        for i in ids:
            if i in seen:
                raise ValueError(f"duplicate id {i!r}")
            seen.add(i)
        if self.main_headline not in self.headlines:
            raise ValueError(f"main_headline {self.main_headline!r} is not in headlines")
        return self

    def bullet_owner(self) -> dict[str, str]:
        return {b.id: e.id for e in self.experience for b in e.bullets}

    def item(self, item_id: str) -> Bullet | Project | Extra | None:
        for e in self.experience:
            for b in e.bullets:
                if b.id == item_id:
                    return b
        return next((x for x in (*self.projects, *self.extras) if x.id == item_id), None)


def load_bank(path: Path) -> Bank:
    if not path.exists():
        raise BankError(f"Missing {path}. Copy cv.example.yaml to {path.name} and fill it in.")
    try:
        return Bank.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except yaml.YAMLError as e:
        raise BankError(f"{path.name} is not valid YAML: {e}") from e
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'file'}: {err['msg']}" for err in e.errors())
        raise BankError(f"{path.name} is invalid: {problems}") from e
