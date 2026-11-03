"""Claude sorts every form field (fact / open / leave) and drafts open answers in the candidate's voice.

Code then enforces the rules Claude must not decide alone: EEO and consent questions are always the
candidate's, facts come from the profile as written, and every number and technology in an answer
must be in the experience bank."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from jobcopilot.apply.models import Answer, FormField, FormSnapshot
from jobcopilot.apply.voice import Voice
from jobcopilot.config import ApplicationProfile
from jobcopilot.cv.bank import Bank
from jobcopilot.cv.guard import check_text
from jobcopilot.cv.tailor import bank_for_prompt
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Job
from jobcopilot.scoring import RETRY_SUFFIX, load_prompt

APPLY_PROMPT_VERSION = "apply_v1"
DESCRIPTION_LIMIT = 6000
DEFAULT_WORDS = 150
EEO_WORDS = ("gender", "sex", "ethnic", "race", "racial", "disabilit", "veteran", "sexual orientation", "pronoun",
             "género", "sexo", "etnia", "discapacidad", "sexe", "origine", "handicap", "orientation sexuelle")
CONSENT_WORDS = ("agree", "agreement", "consent", "terms", "arbitrat", "privacy policy", "acepto", "términos",
                 "consentimiento", "j'accepte", "conditions générales")
CV_WORDS = ("cv", "resume", "résumé", "curriculum")
CHOICE_KINDS = ("select", "radio", "checkbox")
AI_BAN_RULE = ("- This posting asks candidates not to use AI for their answers: do not write any `open` answer. "
               "Return `open` with an empty `answer` for those questions; still classify facts and choices.")
_WORD_GUIDE = re.compile(r"(\d+)\s*(?:words|palabras|mots)", re.I)

SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "kind": {"type": "string", "enum": ["fact", "open", "leave"]},
                    "profile_key": {"type": "string"},
                    "option": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["key", "kind"],
            },
        }
    },
    "required": ["answers"],
}


class _Item(BaseModel):
    key: str
    kind: str
    profile_key: str | None = None
    option: str | None = None
    answer: str | None = None


class _Response(BaseModel):
    answers: list[_Item]


def has_word(label: str, words: tuple[str, ...]) -> bool:
    low = label.lower()
    return any(re.search(rf"(?<![a-zà-ÿ]){re.escape(w)}", low) for w in words)


def word_limit(field: FormField) -> int:
    """150 words, or the largest word count the question's help text asks for ("200-400 words")."""
    numbers = [int(n) for n in re.findall(r"\d+", " ".join(m.group(0) for m in _WORD_GUIDE.finditer(field.description)))]
    guide = re.search(r"(\d+)\s*(?:-|–|to|a|à)\s*(\d+)\s*(?:words|palabras|mots)", field.description, re.I)
    if guide:
        numbers.append(int(guide.group(2)))
    return max(numbers + [DEFAULT_WORDS])


def _limit_text(field: FormField) -> str:
    return f"{field.max_length} characters" if field.max_length else f"{word_limit(field)} words"


def _render(job: Job, fields: list[FormField], profile: ApplicationProfile, bank: Bank, voice: Voice, ai_ban: bool) -> str:
    payload = [
        {"key": f.key, "label": f.label, "help": f.description or None, "kind": f.kind, "options": f.options or None,
         "required": f.required, "limit": _limit_text(f) if f.kind in ("text", "textarea") else None}
        for f in fields
    ]
    values = {
        "profile_keys": ", ".join(profile.values()) or "(none)",
        "default_words": str(DEFAULT_WORDS),
        "ai_ban_rule": AI_BAN_RULE if ai_ban else "",
        "company": job.company,
        "job_title": job.title,
        "description": job.description[:DESCRIPTION_LIMIT],
        "fields": json.dumps(payload, ensure_ascii=False, indent=1),
        "samples": "\n\n---\n\n".join(voice.samples) or "(none yet)",
        "recent": "\n\n".join(f"Q: {q}\nA: {a}" for q, a in voice.recent) or "(none yet)",
        "notes": voice.notes or "(none)",
        "bank": bank_for_prompt(bank),
    }
    prompt = load_prompt(APPLY_PROMPT_VERSION)
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt


def _answer(field: FormField, item: _Item | None, profile: ApplicationProfile, bank: Bank, ai_ban: bool) -> Answer:
    def make(kind, draft="", problems=(), profile_key=None):
        return Answer(field_key=field.key, label=field.label, kind=kind, draft=draft, problems=list(problems),
                      profile_key=profile_key, step=field.step)

    if has_word(field.label, EEO_WORDS) or has_word(field.label, CONSENT_WORDS):
        return make("leave")
    if field.kind == "file":
        return make("cv", "CV") if has_word(field.label, CV_WORDS) else make("leave")
    if item is None or item.kind not in ("fact", "open"):
        return make("leave")
    choice = field.kind in CHOICE_KINDS
    if choice:
        if item.kind == "fact" and not profile.values().get(item.profile_key or ""):
            return make("leave")
        if item.option not in field.options:
            return make("leave", problems=[f"{field.label}: option {item.option!r} is not one of {field.options}"])
        return make(item.kind, item.option, profile_key=item.profile_key if item.kind == "fact" else None)
    if item.kind == "fact":
        value = profile.values().get(item.profile_key or "")
        return make("fact", value, profile_key=item.profile_key) if value else make("leave")
    if ai_ban:
        return make("open")
    text = (item.answer or "").strip()
    problems = check_text(field.label, text, bank, extra_source=" ".join(profile.values().values()))
    if field.max_length and len(text) > field.max_length:
        problems.append(f"{field.label}: {len(text)} characters; maximum is {field.max_length}")
    elif not field.max_length and len(text.split()) > word_limit(field):
        problems.append(f"{field.label}: {len(text.split())} words; maximum is {word_limit(field)}")
    return make("open", text, problems)


_FACT_LABELS = (
    ("first_name", ("first name", "nombre", "prénom")), ("last_name", ("last name", "surname", "apellido", "nom de famille")),
    ("email", ("email", "e-mail", "correo")), ("phone", ("phone", "teléfono", "telephone", "téléphone", "mobile")),
    ("linkedin", ("linkedin",)), ("github", ("github",)), ("portfolio", ("website", "portfolio", "personal site")),
    ("city", ("city", "ciudad", "ville")),
)


def fallback_answers(fields: list[FormField], profile: ApplicationProfile, bank: Bank, error: str) -> list[Answer]:
    """When Claude can't be reached: common facts by label, the CV, and empty open answers saying why."""
    answers = []
    for f in fields:
        key = next((k for k, words in _FACT_LABELS if has_word(f.label, words)), None)
        if key and f.kind not in CHOICE_KINDS and profile.values().get(key):
            answers.append(_answer(f, _Item(key=f.key, kind="fact", profile_key=key), profile, bank, False))
            continue
        answer = _answer(f, None, profile, bank, False)
        if answer.kind == "leave" and f.kind == "textarea" and not has_word(f.label, EEO_WORDS + CONSENT_WORDS):
            answer = answer.model_copy(update={"kind": "open", "problems": [f"Claude unavailable: {error}"]})
        answers.append(answer)
    return answers


def draft_answers(job: Job, form: FormSnapshot, fields: list[FormField], profile: ApplicationProfile, bank: Bank,
                  voice: Voice, llm: LLMClient, model: str, ai_ban: bool) -> list[Answer]:
    """One answer per field in `fields` (a whole form or one step of it), in field order."""
    prompt = _render(job, fields, profile, bank, voice, ai_ban)
    answers: list[Answer] = []
    for attempt in range(2):
        try:
            resp = _Response.model_validate(llm.complete_json(prompt, SCHEMA, model))
        except LLMUnavailable:
            raise
        except ValidationError as e:
            if attempt == 1:
                raise LLMError(f"Claude's answer had the wrong shape twice: {str(e)[:300]}") from e
            prompt += RETRY_SUFFIX.format(error=str(e)[:500])
            continue
        items = {i.key: i for i in resp.answers}
        answers = [_answer(f, items.get(f.key), profile, bank, ai_ban) for f in fields]
        problems = [p for a in answers for p in a.problems]
        if not problems:
            return answers
        if attempt == 0:
            prompt += RETRY_SUFFIX.format(error="it broke these rules: " + "; ".join(problems))
    return answers
