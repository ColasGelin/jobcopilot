"""Data shared by form reading, drafting, storage and the Mac agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FieldKind = Literal["text", "textarea", "select", "radio", "checkbox", "file", "email", "tel", "url"]
AnswerKind = Literal["fact", "open", "leave", "cv"]
PREP_STATUSES = ("reading", "needs_you", "drafting", "ready", "filled", "applied")


class FormField(BaseModel):
    key: str
    selector: str
    label: str
    description: str = ""  # help text shown with the question ("great answers are 200-400 words")
    kind: FieldKind
    options: list[str] = Field(default_factory=list)
    required: bool = False
    max_length: int | None = None
    step: int = 0  # multi-step forms: 0 for the first step read, +1 for each later one
    frame: str = ""  # URL of the iframe holding the field; "" for the main page


class FormSnapshot(BaseModel):
    url: str
    page_text: str = ""
    fields: list[FormField]


class NeedsYou(BaseModel):
    """The form can't be read without the candidate (sign-in, CAPTCHA, no form)."""

    reason: str


class Answer(BaseModel):
    id: int | None = None
    field_key: str
    label: str
    kind: AnswerKind
    profile_key: str | None = None
    draft: str = ""
    final: str = ""
    problems: list[str] = Field(default_factory=list)
    step: int = 0


class PreparedApp(BaseModel):
    id: int
    job_id: str
    status: str
    reason: str | None = None
    form: FormSnapshot | None = None
    ai_ban_quote: str | None = None
    updated_at: datetime


class AgentCommand(BaseModel):
    id: int
    app_id: int
    action: Literal["fill", "read"]
    status: Literal["queued", "taken", "waiting", "done", "failed"]
    result: dict | None = None
    updated_at: datetime
