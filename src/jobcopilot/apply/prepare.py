"""Prepare applications end to end: read the form, tailor the CV, draft every answer, store it all."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from jobcopilot.apply.ai_policy import find_ai_ban, find_ai_policy_field
from jobcopilot.apply.draft import APPLY_PROMPT_VERSION, CV_WORDS, has_word, draft_answers, fallback_answers
from jobcopilot.apply.models import FormField, FormSnapshot, NeedsYou, PreparedApp
from jobcopilot.apply.voice import load_voice
from jobcopilot.config import Profile, Settings
from jobcopilot.cv.bank import Bank, BankError
from jobcopilot.db import DB
from jobcopilot.llm import LLMClient, LLMError, LLMUnavailable
from jobcopilot.models import Job

log = logging.getLogger(__name__)


@dataclass
class Preparer:
    db: DB
    settings: Settings
    profile: Profile
    llm: LLMClient
    read: Callable[[Job], FormSnapshot | NeedsYou]
    bank_loader: Callable[[], Bank]

    def prepare_job(self, job_id: str, now: datetime) -> PreparedApp:
        """Prepare one job. An application that exists is returned as it is, unless it needs the candidate (then it is read again)."""
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError(f"unknown job {job_id!r}")
        existing = self.db.prepared_for_job(job_id)
        if existing and existing.status != "needs_you":
            return existing
        if existing:
            app_id = existing.id
            self.db.update_prepared(app_id, now, status="reading")
        else:
            app_id = self.db.create_prepared(job_id, "reading", now)
        result = self.read(job)
        if isinstance(result, NeedsYou):
            self.db.update_prepared(app_id, now, status="needs_you", reason=result.reason)
            return self.db.get_prepared(app_id)
        self.db.update_prepared(app_id, now, status="drafting", reason=None, form=result)
        return self._draft(app_id, job, result, result.fields, now)

    def ingest(self, app_id: int, form: FormSnapshot, now: datetime) -> PreparedApp:
        """A form step read on the Mac (after a sign-in or a "Next"): add it as the next step and draft it."""
        app = self.db.get_prepared(app_id)
        if app is None:
            raise ValueError(f"unknown application {app_id}")
        old = app.form.fields if app.form else []
        step = max(f.step for f in old) + 1 if old else 0
        new = [f.model_copy(update={"step": step}) for f in form.fields]
        merged = FormSnapshot(url=form.url, page_text=form.page_text, fields=old + new)
        self.db.update_prepared(app_id, now, status="drafting", reason=None, form=merged)
        return self._draft(app_id, self.db.get_job(app.job_id), merged, new, now)

    def prepare_pending(self, now: datetime) -> int:
        """After a daily run: prepare the best new matches, up to the cap. Returns how many became ready."""
        done = 0
        for job_id in self.db.jobs_to_prepare(self.settings.prepare.min_score, self.settings.prepare.max_per_run):
            try:
                done += self.prepare_job(job_id, now).status == "ready"
            except LLMUnavailable as e:
                log.warning("Claude unavailable; stopping preparation: %s", e)
                break
            except Exception:  # one broken page must not stop the others
                log.exception("preparing %s failed", job_id)
        return done

    # --- internals -------------------------------------------------------------
    def _draft(self, app_id: int, job: Job, form: FormSnapshot, fields: list[FormField], now: datetime) -> PreparedApp:
        try:
            bank = self.bank_loader()
        except BankError as e:
            self.db.update_prepared(app_id, now, status="needs_you", reason=f"Experience bank problem: {e}")
            return self.db.get_prepared(app_id)
        quote = find_ai_ban(form.page_text) or find_ai_policy_field(form.fields) or self.db.get_prepared(app_id).ai_ban_quote
        model = self.settings.llm.draft_model
        try:
            cv_problem = self._ensure_cv(job, bank, fields)
            voice = load_voice(self.settings.voice_dir, self.db.voice_examples())
            answers = draft_answers(job, form, fields, self.profile.application, bank, voice, self.llm, model, ai_ban=bool(quote))
        except LLMUnavailable as e:
            self.db.update_prepared(app_id, now, status="needs_you", reason=f"Claude unavailable: {e}", ai_ban_quote=quote)
            raise
        except LLMError as e:
            answers = fallback_answers(fields, self.profile.application, bank, str(e)[:200])
        if cv_problem:
            answers = [a.model_copy(update={"problems": a.problems + [cv_problem]}) if a.kind == "cv" else a for a in answers]
        self.db.save_answers(app_id, answers, model, APPLY_PROMPT_VERSION, now)
        self.db.update_prepared(app_id, now, status="ready", reason=None, ai_ban_quote=quote)
        return self.db.get_prepared(app_id)

    def _ensure_cv(self, job: Job, bank: Bank, fields: list[FormField]) -> str | None:
        """Tailor the CV when the form asks for one and the job has none yet; a problem message if that fails."""
        wants_cv = any(f.kind == "file" and has_word(f.label, CV_WORDS) for f in fields)
        if not wants_cv or self.db.latest_tailored_cv(job.id) is not None:
            return None
        from jobcopilot.cv.render import render_pdf
        from jobcopilot.cv.tailor import TAILOR_PROMPT_VERSION, TailorError, cv_filename, tailor_cv

        model = self.settings.llm.draft_model
        match = self.db.match(job.id)
        try:
            content = tailor_cv(job, match.evaluation if match else None, bank, self.llm, model)
        except LLMUnavailable:
            raise
        except (TailorError, LLMError) as e:
            return f"Tailored CV could not be created: {str(e)[:200]}"
        try:
            result = render_pdf(content)
        except Exception as e:  # keep the content; the dashboard can still render it
            self.db.save_tailored_cv(job.id, content, 0, model, TAILOR_PROMPT_VERSION)
            return f"Tailored CV could not be rendered: {str(e)[:200]}"
        self.settings.cvs_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.cvs_dir / cv_filename(job)).write_bytes(result.pdf)
        self.db.save_tailored_cv(job.id, content, result.pages, model, TAILOR_PROMPT_VERSION)
        return None
