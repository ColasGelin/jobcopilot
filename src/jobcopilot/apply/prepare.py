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
from jobcopilot.db import DB, STALE_PREP_AFTER
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
    fetch: Callable[[str], Job | NeedsYou] | None = None  # a job from a pasted link (apply/fetch.py)

    def add_job(self, url: str, now: datetime) -> tuple[str | None, str | None]:
        """A job from a link the candidate pasted: stored, scored and prepared whatever its score.
        Returns (job id, None), or (None, why it could not be added)."""
        from jobcopilot.scoring import score_jobs

        known = self.db.get_job(Job(url=url.strip(), source="manual", company="", title="").id)
        if known is not None:
            self.prepare_job(known.id, now)
            return known.id, None
        job = self.fetch(url) if self.fetch else NeedsYou(reason="Adding jobs by link is not configured.")
        if isinstance(job, NeedsYou):
            return None, job.reason
        self.db.insert_jobs([job], now)
        try:
            evaluations, _ = score_jobs([job], self.profile, self.llm, self.settings.llm.score_model)
            for e in evaluations:
                self.db.save_evaluation(e)
        except LLMError as e:  # unscored is fine: the candidate chose this job
            log.warning("scoring %s failed: %s", job.id, e)
        self.prepare_job(job.id, now)
        return job.id, None

    def prepare_job(self, job_id: str, now: datetime) -> PreparedApp:
        """Prepare one job. An application that exists is returned as it is, unless it needs the candidate (then it is read again)."""
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError(f"unknown job {job_id!r}")
        existing = self.db.prepared_for_job(job_id)
        if existing and not self._retryable(existing, now):
            return existing
        if existing:
            app_id = existing.id
            self.db.update_prepared(app_id, now, status="reading")
        else:
            app_id = self.db.create_prepared(job_id, "reading", now)
        try:
            result = self.read(job)
            if isinstance(result, NeedsYou):
                self.db.update_prepared(app_id, now, status="needs_you", reason=result.reason)
                return self.db.get_prepared(app_id)
            self.db.delete_answers(app_id)  # a re-read replaces the earlier answers
            self.db.update_prepared(app_id, now, status="drafting", reason=None, form=result)
            return self._draft(app_id, job, result, result.fields, now)
        except LLMUnavailable:
            raise
        except Exception as e:  # never leave it "reading"/"drafting": the card would wait forever
            log.exception("preparing %s failed", job_id)
            self.db.update_prepared(app_id, now, status="needs_you", reason=f"Preparing failed: {str(e)[:200] or repr(e)}")
            return self.db.get_prepared(app_id)

    @staticmethod
    def _retryable(app: PreparedApp, now: datetime) -> bool:
        stuck = app.status in ("reading", "drafting") and app.updated_at < now - STALE_PREP_AFTER
        return app.status == "needs_you" or stuck

    def write_cover(self, job_id: str, now: datetime) -> None:
        """Draft (or redo) the job's cover letter in the candidate's voice; stored with any fact-check problems."""
        from jobcopilot.apply.cover import COVER_PROMPT_VERSION, write_cover_letter

        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError(f"unknown job {job_id!r}")
        voice = load_voice(self.settings.voice_dir, self.db.voice_examples())
        model = self.settings.llm.draft_model
        app = self.db.prepared_for_job(job_id)
        answers = [(a.label, a.final) for a in self.db.answers(app.id) if a.kind == "open" and a.final.strip()] if app else []
        text, problems = write_cover_letter(job, self.bank_loader(), self.profile.application, voice, self.llm, model,
                                            candidate=self.profile.summary, answers=answers)
        self.db.save_cover_letter(job_id, text, problems, model, COVER_PROMPT_VERSION, now)

    def ingest(self, app_id: int, form: FormSnapshot, now: datetime) -> PreparedApp:
        """A form step read on the Mac (after a sign-in or a "Next"): add it as the next step and draft it."""
        app = self.db.get_prepared(app_id)
        if app is None:
            raise ValueError(f"unknown application {app_id}")
        old = app.form.fields if app.form else []
        last = max((f.step for f in old), default=-1)
        if last >= 0 and {f.key for f in form.fields} == {f.key for f in old if f.step == last}:
            step = last  # the same page read twice (Continue clicked again): replace that step
        else:
            step = last + 1
        new = [f.model_copy(update={"step": step}) for f in form.fields]
        merged = FormSnapshot(url=form.url, page_text=form.page_text, fields=[f for f in old if f.step != step] + new)
        self.db.update_prepared(app_id, now, status="drafting", reason=None, form=merged)
        try:
            self.db.delete_answers(app_id, {step})
            return self._draft(app_id, self.db.get_job(app.job_id), merged, new, now)
        except LLMUnavailable:
            if app.status in ("ready", "filled"):  # keep the application usable; the agent reports the error
                self.db.update_prepared(app_id, now, status=app.status, reason=None, form=app.form or merged)
            raise

    def prepare_pending(self, now: datetime) -> int:
        """After a daily run: prepare the best new matches, up to the cap. Returns how many became ready."""
        done = 0
        for job_id in self.db.jobs_to_prepare(self.settings.prepare.min_score, self.settings.prepare.max_per_run, now):
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
            letter = self.db.cover_letter(job.id)
            answers = draft_answers(job, form, fields, self.profile.application, bank, voice, self.llm, model, ai_ban=bool(quote),
                                    cover_letter=letter["final"] if letter else "")
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
