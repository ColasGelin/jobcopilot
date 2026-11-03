"""CvContent: exactly what one rendered CV shows, whether main or tailored."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from jobcopilot.cv.bank import Bank, Certification, Contact, Education, Language, Project, SkillGroup


class ExperienceBlock(BaseModel):
    title: str
    company: str
    location: str = ""
    dates: str
    bullets: list[str]


class ProjectItem(BaseModel):
    name: str
    text: str
    in_progress: bool = False


class CvContent(BaseModel):
    language: Literal["en", "es", "fr"] = "en"
    contact: Contact
    headline: str
    summary: str
    experience: list[ExperienceBlock]
    engineering: list[ProjectItem] = []
    hackathons: list[ProjectItem] = []
    skills: list[SkillGroup] = []
    education: list[Education] = []
    certifications: list[Certification] = []
    languages: list[Language] = []
    other: list[str] = []
    interests: list[str] = []


def project_item(p: Project, text: str | None = None) -> ProjectItem:
    return ProjectItem(name=p.name, text=p.text if text is None else text, in_progress=p.status == "in_progress")


def main_content(bank: Bank) -> CvContent:
    """The main CV: every item marked `main`, in bank order."""
    main_projects = [p for p in bank.projects if p.main]
    return CvContent(
        contact=bank.contact,
        headline=bank.main_headline,
        summary=bank.summary,
        experience=[
            ExperienceBlock(title=e.title, company=e.company, location=e.location, dates=e.dates,
                            bullets=[b.text for b in e.bullets if b.main])
            for e in bank.experience
        ],
        engineering=[project_item(p) for p in main_projects if p.section == "engineering"],
        hackathons=[project_item(p) for p in main_projects if p.section == "hackathons"],
        skills=bank.skills,
        education=bank.education,
        certifications=bank.certifications,
        languages=bank.languages,
        other=[x.text for x in bank.extras if x.main and x.section == "other"],
        interests=[x.text for x in bank.extras if x.main and x.section == "interests"],
    )
