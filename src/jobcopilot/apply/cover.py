"""Cover letter on request: drafted in the candidate's voice from the experience bank, rendered like the CV."""

from __future__ import annotations

import re
from datetime import date

from jobcopilot.apply.overlap import copied, repeats
from jobcopilot.apply.voice import Voice
from jobcopilot.config import ApplicationProfile
from jobcopilot.cv.bank import Bank, Contact
from jobcopilot.cv.guard import check_text
from jobcopilot.cv.render import FONTS_DIR, TEMPLATES_DIR, _env
from jobcopilot.cv.tailor import bank_for_prompt, download_name
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Job
from jobcopilot.scoring import RETRY_SUFFIX, load_prompt

COVER_PROMPT_VERSION = "cover_v3"
MAX_WORDS = 300
DESCRIPTION_LIMIT = 6000
SCHEMA = {"type": "object", "properties": {"letter": {"type": "string"}}, "required": ["letter"]}


def _render_prompt(job: Job, bank: Bank, voice: Voice, candidate: str, answers: list[tuple[str, str]]) -> str:
    values = {
        "answers": "\n\n".join(f"Q: {q}\nA: {a}" for q, a in answers) or "(none)",
        "candidate": candidate.strip() or "(not given)",
        "max_words": str(MAX_WORDS),
        "name": bank.contact.name,
        "company": job.company,
        "job_title": job.title,
        "description": job.description[:DESCRIPTION_LIMIT],
        "samples": "\n\n---\n\n".join(voice.samples) or "(none yet)",
        "recent": "\n\n".join(f"Q: {q}\nA: {a}" for q, a in voice.recent) or "(none yet)",
        "notes": voice.notes or "(none)",
        "bank": bank_for_prompt(bank),
    }
    prompt = load_prompt(COVER_PROMPT_VERSION)
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt


def _problems(text: str, bank: Bank, profile: ApplicationProfile, company: str, posting: str) -> list[str]:
    # Company facts may come from the posting; the prompt forbids presenting them as the candidate's own.
    problems = check_text("cover letter", text, bank, extra_source=" ".join([company, posting, *profile.values().values()]))
    words = len(text.split())
    if words > MAX_WORDS:
        problems.append(f"cover letter: {words} words; maximum is {MAX_WORDS}")
    return problems


def write_cover_letter(job: Job, bank: Bank, profile: ApplicationProfile, voice: Voice, llm: LLMClient,
                       model: str, candidate: str = "", answers: list[tuple[str, str]] = ()) -> tuple[str, list[str]]:
    """(letter, problems). `candidate` is what they look for (profile summary), so the "why" fits them.
    After one retry, a letter that still breaks a rule is kept with its problems shown."""
    answers = list(answers)
    prompt = _render_prompt(job, bank, voice, candidate, answers)
    text, problems = "", []
    for attempt in range(2):
        try:
            data = llm.complete_json(prompt, SCHEMA, model)
            text = str(data["letter"]).strip()
        except LLMUnavailable:
            raise
        except (LLMError, KeyError, TypeError) as e:
            if attempt == 1:
                raise LLMError(f"Claude's answer was invalid twice: {e}") from e
            prompt += RETRY_SUFFIX.format(error=str(e)[:300])
            continue
        problems = _problems(text, bank, profile, job.company, job.description[:DESCRIPTION_LIMIT])
        problems += repeats("cover letter", text, answers) + copied("cover letter", text, job.description)
        if not problems:
            break
        if attempt == 0:
            prompt += RETRY_SUFFIX.format(error="it broke these rules: " + "; ".join(problems))
    return text, problems


def render_cover(contact: Contact, job: Job, text: str, today: date) -> bytes:
    from weasyprint import HTML

    css = (TEMPLATES_DIR / "cv.css").read_text(encoding="utf-8").replace("{font_base}", FONTS_DIR.as_uri() + "/")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    html = _env.get_template("cover.html").render(
        contact=contact, company=job.company, title=job.title, paragraphs=paragraphs, today=today.strftime("%d/%m/%Y"),
        css=css, language="en",
    )
    return HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()


_COVER_FIELD = re.compile(r"cover|motivation|carta de presentaci|lettre", re.I)


def is_cover_field(label: str) -> bool:
    return bool(_COVER_FIELD.search(label))


def cover_filename(name: str, company: str = "") -> str:
    return download_name(name, company, kind="Cover-Letter")
