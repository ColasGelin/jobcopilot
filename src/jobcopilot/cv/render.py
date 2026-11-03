"""Render CvContent to HTML (preview) and PDF (WeasyPrint)."""

from __future__ import annotations

from dataclasses import dataclass
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


def render_html(content: CvContent, for_web: bool = False) -> str:
    font_base = "/cv-assets/" if for_web else FONTS_DIR.as_uri() + "/"
    css = (TEMPLATES_DIR / "cv.css").read_text(encoding="utf-8").replace("{font_base}", font_base)
    return _env.get_template("cv.html").render(cv=content, t=SECTION_TITLES[content.language] | content.section_titles.get(content.language, {}), css=css)


def render_pdf(content: CvContent) -> RenderResult:
    from weasyprint import HTML  # imported lazily: needs Pango, only loaded when rendering

    document = HTML(string=render_html(content), base_url=str(TEMPLATES_DIR)).render()
    return RenderResult(pdf=document.write_pdf(), pages=len(document.pages))
