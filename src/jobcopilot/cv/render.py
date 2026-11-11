"""Render CvContent to HTML (preview) and PDF (WeasyPrint)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobcopilot.cv.content import CvContent

HERE = Path(__file__).parent
FONTS_DIR = HERE / "fonts"
TEMPLATES_DIR = HERE / "templates"

SECTION_TITLES: dict[str, dict[str, str]] = {
    "en": {
        "experience": "Experience", "engineering": "Projects",
        "hackathons": "Hackathons", "other": "Other experience", "skills": "Skills",
        "education": "Education", "certifications": "Certifications", "languages": "Languages",
        "interests": "Interests", "in_progress": "(in progress)",
    },
    "es": {
        "experience": "Experiencia", "engineering": "Proyectos",
        "hackathons": "Hackathons", "other": "Otra experiencia", "skills": "Competencias",
        "education": "Formación", "certifications": "Certificaciones", "languages": "Idiomas",
        "interests": "Intereses", "in_progress": "(en curso)",
    },
    "fr": {
        "experience": "Expérience", "engineering": "Projets",
        "hackathons": "Hackathons", "other": "Autres expériences", "skills": "Compétences",
        "education": "Formation", "certifications": "Certifications", "languages": "Langues",
        "interests": "Centres d'intérêt", "in_progress": "(en cours)",
    },
}

_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))


@dataclass
class RenderResult:
    pdf: bytes
    pages: int
    content: CvContent  # what the PDF shows: the given content, shortened when it ran past one page
    removed: list[str] = field(default_factory=list)


# Tighter layouts tried before any content is removed; each level adds to the previous one.
DENSER = [
    "@page { margin: 12mm 14mm 10mm; } body { line-height: 1.36; } .summary { margin: 7pt 0 3pt; }"
    " h2 { margin: 9pt 0 4pt; } .job { margin-bottom: 5pt; } li { margin-bottom: 1pt; } .project, .skill { margin-bottom: 4pt; }",
    "body { font-size: 8.6pt; line-height: 1.32; } .job-title { font-size: 8.9pt; }",
]


def render_html(content: CvContent, for_web: bool = False) -> str:
    font_base = "/cv-assets/" if for_web else FONTS_DIR.as_uri() + "/"
    css = (TEMPLATES_DIR / "cv.css").read_text(encoding="utf-8").replace("{font_base}", font_base)
    css += "\n" + "\n".join(DENSER[: content.density])
    return _env.get_template("cv.html").render(cv=content, t=SECTION_TITLES[content.language] | content.section_titles.get(content.language, {}), css=css)


def _drop_last(field_name: str, keep: int):
    def step(cv: CvContent) -> tuple[CvContent, str] | None:
        items = getattr(cv, field_name)
        if len(items) <= keep:
            return None
        last = items[-1]
        label = getattr(last, "name", None) or getattr(last, "group", None) or str(last)
        return cv.model_copy(update={field_name: items[:-1]}), f"{field_name}: {label[:60]}"
    return step


def _drop_bullet(keep: int):
    """The last (least relevant) bullet of the experience with the most bullets; the older one on a tie."""
    def step(cv: CvContent) -> tuple[CvContent, str] | None:
        longest = max(range(len(cv.experience)), key=lambda i: (len(cv.experience[i].bullets), i), default=None)
        if longest is None or len(cv.experience[longest].bullets) <= keep:
            return None
        job = cv.experience[longest]
        experience = list(cv.experience)
        experience[longest] = job.model_copy(update={"bullets": job.bullets[:-1]})
        return cv.model_copy(update={"experience": experience}), f"bullet in {job.company}: {job.bullets[-1][:60]}"
    return step


# Least valuable first. Bullets, projects and skill groups are ordered most relevant first, so the last one goes.
TRIM_STEPS = [
    _drop_last("hackathons", 0), _drop_last("other", 0), _drop_last("engineering", 1), _drop_bullet(2),
    _drop_last("skills", 3), _drop_last("interests", 0), _drop_last("certifications", 0), _drop_bullet(1),
    _drop_last("engineering", 0), _drop_last("skills", 1),
]


class CvTooLong(Exception):
    """Even with every optional item removed, the CV does not fit on one page."""


def _render(content: CvContent):
    from weasyprint import HTML  # imported lazily: needs Pango, only loaded when rendering

    return HTML(string=render_html(content), base_url=str(TEMPLATES_DIR)).render()


def render_pdf(content: CvContent, one_page: bool = True) -> RenderResult:
    """A one-page PDF: when the content runs over, the layout is tightened first, then the least relevant items
    are removed until it fits. `one_page=False` renders the content as it is, for a CV that was already sent."""
    removed: list[str] = []
    document = _render(content)
    while one_page and len(document.pages) > 1 and content.density < len(DENSER):
        content = content.model_copy(update={"density": content.density + 1})
        document = _render(content)
    while one_page and len(document.pages) > 1:
        trimmed = next((r for r in (s(content) for s in TRIM_STEPS) if r is not None), None)
        if trimmed is None:
            raise CvTooLong(f"the CV is {len(document.pages)} pages even after removing: {', '.join(removed) or 'nothing'}")
        content, what = trimmed
        removed.append(what)
        document = _render(content)
    return RenderResult(pdf=document.write_pdf(), pages=len(document.pages), content=content, removed=removed)
