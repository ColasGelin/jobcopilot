"""SQLite storage for jobs, evaluations, drafts, applications and runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from jobcopilot.apply.models import AgentCommand, Answer, FormSnapshot, PreparedApp
from jobcopilot.cv.content import CvContent
from jobcopilot.models import STATUSES, Draft, Evaluation, Job, Match, Run
from jobcopilot.text import normalize

FOLLOW_UP_AFTER = timedelta(days=5)
STALE_COMMAND_AFTER = timedelta(minutes=10)
STALE_PREP_AFTER = timedelta(minutes=15)  # an application still "reading"/"drafting" after this was interrupted  # a "taken" command the agent never finished (crash) is offered again
NOT_TO_PREPARE = ("skipped", "rejected", "applied", "interview", "offer", "unavailable")
_KEEP = object()
PIPELINE_STATUSES = ("shortlisted", "applied", "interview", "offer", "rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, source TEXT, company TEXT, title TEXT,
    company_key TEXT, title_key TEXT, location TEXT, remote INTEGER, salary_text TEXT,
    description TEXT, posted_at TEXT, first_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_company_title ON jobs(company_key, title_key);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY, job_id TEXT REFERENCES jobs(id), score INTEGER, fit_reasons TEXT,
    gaps TEXT, model TEXT, prompt_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY, job_id TEXT REFERENCES jobs(id), cv_tweaks TEXT, cover_note TEXT,
    outreach_message TEXT, contact_hint TEXT, model TEXT, prompt_version TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), status TEXT NOT NULL, applied_at TEXT,
    next_follow_up_at TEXT, notes TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS company_checks (name TEXT PRIMARY KEY, checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tailored_cvs (
    id INTEGER PRIMARY KEY, job_id TEXT REFERENCES jobs(id), language TEXT, content TEXT, pages INTEGER,
    model TEXT, prompt_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS prepared_apps (
    id INTEGER PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), status TEXT NOT NULL, reason TEXT,
    form TEXT, ai_ban_quote TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS prepared_answers (
    id INTEGER PRIMARY KEY, app_id INTEGER NOT NULL REFERENCES prepared_apps(id), field_key TEXT, label TEXT,
    kind TEXT, profile_key TEXT, draft TEXT, final TEXT, problems TEXT, step INTEGER, model TEXT,
    prompt_version TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_commands (
    id INTEGER PRIMARY KEY, app_id INTEGER NOT NULL REFERENCES prepared_apps(id), action TEXT NOT NULL,
    status TEXT NOT NULL, result TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS cover_letters (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), draft TEXT, final TEXT, problems TEXT, model TEXT,
    prompt_version TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sent_records (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), saved_at TEXT NOT NULL, cv TEXT, cover TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sent_answers (
    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), question TEXT NOT NULL, answer TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_events (
    message_id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), outcome TEXT NOT NULL, sender TEXT, subject TEXT,
    received_at TEXT, evidence TEXT, from_status TEXT, to_status TEXT, from_applied_at TEXT, from_follow_up_at TEXT,
    created_at TEXT, undone_at TEXT
);
CREATE TABLE IF NOT EXISTS mail_checks (id INTEGER PRIMARY KEY CHECK (id = 1), checked_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, fetched INTEGER, new INTEGER,
    kept INTEGER, scored INTEGER, drafted INTEGER, failed_sources TEXT, error TEXT
);
"""

_JOB_COLS = "url, source, company, title, location, remote, salary_text, description, posted_at"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _key(job: Job) -> tuple[str, str]:
    return normalize(job.company), normalize(job.title)


class DB:
    def __init__(self, path: Path | str):
        # timeout + WAL: the daemon and the dashboard share this file.
        self.conn = sqlite3.connect(str(path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        if "apply_url" not in columns:  # added after the first release: the application page the candidate gave
            try:
                self.conn.execute("ALTER TABLE jobs ADD COLUMN apply_url TEXT")
                self.conn.commit()
            except sqlite3.OperationalError:  # the other container added it first
                pass
        for row in self.conn.execute(  # applications made before records existed
            "SELECT job_id, applied_at FROM applications WHERE applied_at IS NOT NULL"
            " AND job_id NOT IN (SELECT job_id FROM sent_records)"
        ).fetchall():
            self.record_application(row[0], _dt(row[1]))

    # --- jobs ---------------------------------------------------------------
    def filter_new(self, jobs: list[Job]) -> list[Job]:
        seen_urls, seen_keys, fresh = set(), set(), []
        for job in jobs:
            key = _key(job)
            if job.url in seen_urls or key in seen_keys:
                continue
            seen_urls.add(job.url)
            seen_keys.add(key)
            exists = self.conn.execute(
                "SELECT 1 FROM jobs WHERE url = ? OR (company_key = ? AND title_key = ?)", (job.url, *key)
            ).fetchone()
            if not exists:
                fresh.append(job)
        return fresh

    def insert_jobs(self, jobs: list[Job], seen_at: datetime) -> None:
        with self.conn:
            for j in jobs:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, url, source, company, title, company_key, title_key, location,"
                    " remote, salary_text, description, posted_at, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j.id, j.url, j.source, j.company, j.title, *_key(j), j.location, int(j.remote),
                     j.salary_text, j.description, _iso(j.posted_at), _iso(seen_at)),
                )
                if cur.rowcount:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO applications (job_id, status, updated_at) VALUES (?, 'new', ?)",
                        (j.id, _iso(seen_at)),
                    )

    def delete_jobs(self, job_ids: list[str]) -> None:
        with self.conn:
            for table in ("prepared_answers", "agent_commands"):
                self.conn.executemany(
                    f"DELETE FROM {table} WHERE app_id IN (SELECT id FROM prepared_apps WHERE job_id = ?)", [(i,) for i in job_ids]
                )
            for table, col in (("sent_answers", "job_id"), ("sent_records", "job_id"), ("cover_letters", "job_id"), ("prepared_apps", "job_id"), ("tailored_cvs", "job_id"), ("drafts", "job_id"), ("evaluations", "job_id"), ("applications", "job_id"), ("jobs", "id")):
                self.conn.executemany(f"DELETE FROM {table} WHERE {col} = ?", [(i,) for i in job_ids])

    def get_job(self, job_id: str) -> Job | None:
        row = self.conn.execute(f"SELECT {_JOB_COLS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["remote"] = bool(data["remote"])
        data["posted_at"] = _dt(data["posted_at"])
        return Job(**data)

    def set_apply_url(self, job_id: str, url: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE jobs SET apply_url = ? WHERE id = ?", (url.strip() or None, job_id))

    def apply_url(self, job_id: str) -> str:
        """The application page the candidate gave for this job, or ""."""
        row = self.conn.execute("SELECT apply_url FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return (row[0] or "") if row else ""

    def form_url(self, job_id: str) -> str | None:
        """Where the application form is: the link the candidate gave, else the job's own URL."""
        row = self.conn.execute("SELECT coalesce(apply_url, url) FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row[0] if row else None

    def set_description(self, job_id: str, description: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))

    def known_urls(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT url FROM jobs")}

    # --- tailored CVs ---------------------------------------------------------
    def save_tailored_cv(self, job_id: str, content: CvContent, pages: int, model: str, prompt_version: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO tailored_cvs (job_id, language, content, pages, model, prompt_version) VALUES (?,?,?,?,?,?)",
                (job_id, content.language, content.model_dump_json(), pages, model, prompt_version),
            )

    def latest_tailored_cv(self, job_id: str) -> tuple[CvContent, int, datetime] | None:
        """(content, pages, created_at UTC) of the newest tailored CV for the job."""
        row = self.conn.execute(
            "SELECT content, pages, created_at FROM tailored_cvs WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            content = CvContent.model_validate_json(row["content"])
        except ValidationError:  # saved by an older version whose format no longer loads
            return None
        created = datetime.fromisoformat(row["created_at"]).replace(tzinfo=timezone.utc)
        return content, row["pages"], created

    # --- evaluations and drafts --------------------------------------------
    def save_evaluation(self, e: Evaluation) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO evaluations (job_id, score, fit_reasons, gaps, model, prompt_version) VALUES (?,?,?,?,?,?)",
                (e.job_id, e.score, e.fit_reasons, e.gaps, e.model, e.prompt_version),
            )

    def save_draft(self, d: Draft) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO drafts (job_id, cv_tweaks, cover_note, outreach_message, contact_hint, model, prompt_version)"
                " VALUES (?,?,?,?,?,?,?)",
                (d.job_id, json.dumps(d.cv_tweaks), d.cover_note, d.outreach_message, d.contact_hint, d.model, d.prompt_version),
            )

    def latest_draft(self, job_id: str) -> Draft | None:
        row = self.conn.execute(
            "SELECT job_id, cv_tweaks, cover_note, outreach_message, contact_hint, model, prompt_version"
            " FROM drafts WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["cv_tweaks"] = json.loads(data["cv_tweaks"])
        return Draft(**data)

    # --- prepared applications ------------------------------------------------
    def create_prepared(self, job_id: str, status: str, now: datetime, reason: str | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO prepared_apps (job_id, status, reason, created_at, updated_at) VALUES (?,?,?,?,?)",
                (job_id, status, reason, _iso(now), _iso(now)),
            )
        return cur.lastrowid

    def _prepared(self, where: str, value) -> PreparedApp | None:
        row = self.conn.execute(
            f"SELECT id, job_id, status, reason, form, ai_ban_quote, updated_at FROM prepared_apps WHERE {where} = ?", (value,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["form"] = FormSnapshot.model_validate_json(data["form"]) if data["form"] else None
        data["updated_at"] = _dt(data["updated_at"])
        return PreparedApp(**data)

    def get_prepared(self, app_id: int) -> PreparedApp | None:
        return self._prepared("id", app_id)

    def prepared_for_job(self, job_id: str) -> PreparedApp | None:
        return self._prepared("job_id", job_id)

    def update_prepared(self, app_id: int, now: datetime, *, status: str | None = None, reason=_KEEP,
                        form: FormSnapshot | None = None, ai_ban_quote=_KEEP) -> None:
        """Change only the given fields (reason and ai_ban_quote may be set to None explicitly)."""
        changes: dict[str, object] = {"updated_at": _iso(now)}
        if status is not None:
            changes["status"] = status
        if reason is not _KEEP:
            changes["reason"] = reason
        if form is not None:
            changes["form"] = form.model_dump_json()
        if ai_ban_quote is not _KEEP:
            changes["ai_ban_quote"] = ai_ban_quote
        with self.conn:
            self.conn.execute(
                f"UPDATE prepared_apps SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?", (*changes.values(), app_id)
            )

    def delete_answers(self, app_id: int, steps: set[int] | None = None) -> None:
        """Before drafting a form (or one step of it) again, so answers are never duplicated."""
        with self.conn:
            if steps is None:
                self.conn.execute("DELETE FROM prepared_answers WHERE app_id = ?", (app_id,))
            else:
                self.conn.executemany("DELETE FROM prepared_answers WHERE app_id = ? AND step = ?", [(app_id, s) for s in steps])

    def save_answers(self, app_id: int, answers: list[Answer], model: str, prompt_version: str, now: datetime) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO prepared_answers (app_id, field_key, label, kind, profile_key, draft, final, problems, step,"
                " model, prompt_version, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(app_id, a.field_key, a.label, a.kind, a.profile_key, a.draft, a.draft, json.dumps(a.problems), a.step,
                  model, prompt_version, _iso(now)) for a in answers],
            )

    def answers(self, app_id: int) -> list[Answer]:
        rows = self.conn.execute(
            "SELECT id, field_key, label, kind, profile_key, draft, final, problems, step FROM prepared_answers"
            " WHERE app_id = ? ORDER BY id",
            (app_id,),
        ).fetchall()
        return [Answer(**(dict(r) | {"problems": json.loads(r["problems"] or "[]")})) for r in rows]

    def set_final(self, answer_id: int, text: str, now: datetime) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE prepared_answers SET final = ?, updated_at = ? WHERE id = ?", (text, _iso(now), answer_id)
            )
        return cur.rowcount > 0

    def answer_app_id(self, answer_id: int) -> int | None:
        row = self.conn.execute("SELECT app_id FROM prepared_answers WHERE id = ?", (answer_id,)).fetchone()
        return row[0] if row else None

    def voice_examples(self, limit: int = 5) -> list[tuple[str, str]]:
        """(label, final) of the candidate's latest open answers on applications they went on to fill."""
        rows = self.conn.execute(
            "SELECT a.label, a.final FROM prepared_answers a JOIN prepared_apps p ON p.id = a.app_id"
            " WHERE a.kind = 'open' AND trim(coalesce(a.final, '')) != '' AND p.status IN ('filled', 'applied')"
            " ORDER BY a.updated_at DESC, a.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def jobs_to_prepare(self, min_score: int, limit: int, now: datetime | None = None) -> list[str]:
        """Good matches with no application yet, plus applications to retry: Claude was unavailable, or the
        preparation was interrupted (still reading/drafting after STALE_PREP_AFTER)."""
        stale = _iso(now - STALE_PREP_AFTER) if now else ""
        rows = self.conn.execute(
            "SELECT e.job_id FROM evaluations e"
            " WHERE e.id = (SELECT max(id) FROM evaluations WHERE job_id = e.job_id) AND e.score >= ?"
            " AND NOT EXISTS (SELECT 1 FROM prepared_apps p WHERE p.job_id = e.job_id"
            "   AND NOT (p.status = 'needs_you' AND p.reason LIKE 'Claude unavailable%')"
            "   AND NOT (p.status IN ('reading', 'drafting') AND p.updated_at < ?))"
            f" AND coalesce((SELECT status FROM applications a WHERE a.job_id = e.job_id), 'new') NOT IN ({','.join('?' * len(NOT_TO_PREPARE))})"
            " ORDER BY e.score DESC, e.id DESC LIMIT ?",
            (min_score, stale, *NOT_TO_PREPARE, limit),
        ).fetchall()
        return [r[0] for r in rows]

    # --- cover letters --------------------------------------------------------
    def save_cover_letter(self, job_id: str, draft: str, problems: list[str], model: str, prompt_version: str, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO cover_letters (job_id, draft, final, problems, model, prompt_version, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (job_id, draft, draft, json.dumps(problems), model, prompt_version, _iso(now)),
            )

    def cover_letter(self, job_id: str) -> dict | None:
        row = self.conn.execute("SELECT draft, final, problems FROM cover_letters WHERE job_id = ?", (job_id,)).fetchone()
        return {"draft": row[0], "final": row[1], "problems": json.loads(row[2] or "[]")} if row else None

    def set_cover_final(self, job_id: str, text: str, now: datetime) -> bool:
        with self.conn:
            cur = self.conn.execute("UPDATE cover_letters SET final = ?, updated_at = ? WHERE job_id = ?", (text, _iso(now), job_id))
        return cur.rowcount > 0

    # --- agent commands -------------------------------------------------------
    def queue_command(self, app_id: int, action: str, now: datetime) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO agent_commands (app_id, action, status, created_at, updated_at) VALUES (?,?,'queued',?,?)",
                (app_id, action, _iso(now), _iso(now)),
            )
        return cur.lastrowid

    def _command(self, row) -> AgentCommand | None:
        if row is None:
            return None
        data = dict(row)
        data["result"] = json.loads(data["result"]) if data["result"] else None
        data["updated_at"] = _dt(data["updated_at"])
        return AgentCommand(**data)

    def get_command(self, cmd_id: int) -> AgentCommand | None:
        return self._command(self.conn.execute(
            "SELECT id, app_id, action, status, result, updated_at FROM agent_commands WHERE id = ?", (cmd_id,)).fetchone())

    def latest_command(self, app_id: int) -> AgentCommand | None:
        return self._command(self.conn.execute(
            "SELECT id, app_id, action, status, result, updated_at FROM agent_commands WHERE app_id = ? ORDER BY id DESC LIMIT 1",
            (app_id,)).fetchone())

    def take_next_command(self, now: datetime) -> AgentCommand | None:
        with self.conn:
            row = self.conn.execute(
                "SELECT id FROM agent_commands WHERE status = 'queued' OR (status = 'taken' AND updated_at < ?)"
                " ORDER BY id LIMIT 1",
                (_iso(now - STALE_COMMAND_AFTER),),
            ).fetchone()
            if row is None:
                return None
            self.conn.execute("UPDATE agent_commands SET status = 'taken', updated_at = ? WHERE id = ?", (_iso(now), row[0]))
        return self.get_command(row[0])

    def set_command(self, cmd_id: int, status: str, now: datetime, result: dict | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE agent_commands SET status = ?, result = coalesce(?, result), updated_at = ? WHERE id = ?",
                (status, json.dumps(result) if result is not None else None, _iso(now), cmd_id),
            )

    # --- applications -------------------------------------------------------
    def status(self, job_id: str) -> str | None:
        row = self.conn.execute("SELECT status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        return row[0] if row else None

    def notes(self, job_id: str) -> str | None:
        row = self.conn.execute("SELECT notes FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        return row[0] if row else None

    def mark_llm_error(self, job_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE applications SET notes = 'llm_error' WHERE job_id = ?", (job_id,))

    def set_status(self, job_id: str, status: str, now: datetime) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        with self.conn:
            if status == "applied":
                self.conn.execute(
                    "UPDATE applications SET status = ?, applied_at = ?, next_follow_up_at = ?, updated_at = ? WHERE job_id = ?",
                    (status, _iso(now), _iso(now + FOLLOW_UP_AFTER), _iso(now), job_id),
                )
            else:
                self.conn.execute(
                    "UPDATE applications SET status = ?, updated_at = ? WHERE job_id = ?", (status, _iso(now), job_id)
                )
        if status == "applied":
            self.record_application(job_id, now)

    # --- what was sent ----------------------------------------------------------
    def record_application(self, job_id: str, now: datetime) -> bool:
        """Copy the answers, cover letter and CV as they are now into a record of their own, once.
        Later drafts never change it; only the candidate does, on the record's page."""
        if self.conn.execute("SELECT 1 FROM sent_records WHERE job_id = ?", (job_id,)).fetchone():
            return False
        # The same role often comes from two boards; the work may sit on the other copy.
        same_role = [job_id] + [r[0] for r in self.conn.execute(
            "SELECT b.id FROM jobs a JOIN jobs b ON b.company_key = a.company_key AND b.title_key = a.title_key"
            " WHERE a.id = ? AND b.id != a.id ORDER BY b.first_seen_at", (job_id,))]

        def first(query: str):
            return next((r for r in (self.conn.execute(query, (j,)).fetchone() for j in same_role) if r), None)

        cv = first("SELECT content FROM tailored_cvs WHERE job_id = ? ORDER BY id DESC LIMIT 1")
        cover = first("SELECT final FROM cover_letters WHERE job_id = ? AND trim(final) != ''")
        answers: list[tuple[str, str, str]] = []
        for j in same_role:
            rows = self.conn.execute(
                "SELECT a.label, a.field_key, a.draft, a.final FROM prepared_answers a JOIN prepared_apps p ON p.id = a.app_id"
                " WHERE p.job_id = ? AND a.kind IN ('fact', 'open') ORDER BY a.id", (j,)).fetchall()
            answers = [(r["label"] or r["field_key"], r["final"] if r["final"].strip() else r["draft"],
                        "form" if r["final"].strip() else "draft") for r in rows if (r["final"] or r["draft"] or "").strip()]
            if answers:
                break
        try:
            with self.conn:
                self.conn.execute("INSERT INTO sent_records (job_id, saved_at, cv, cover) VALUES (?,?,?,?)",
                                  (job_id, _iso(now), cv[0] if cv else None, cover[0] if cover else ""))
                self.conn.executemany("INSERT INTO sent_answers (job_id, question, answer, source) VALUES (?,?,?,?)",
                                      [(job_id, *a) for a in answers])
        except sqlite3.IntegrityError:  # the other container saved it first
            return False
        return True

    def has_sent_record(self, job_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sent_records WHERE job_id = ?", (job_id,)).fetchone() is not None

    def sent_record(self, job_id: str) -> dict | None:
        row = self.conn.execute("SELECT saved_at, cv, cover, notes FROM sent_records WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        try:
            cv = CvContent.model_validate_json(row["cv"]) if row["cv"] else None
        except ValidationError:
            cv = None
        answers = self.conn.execute(
            "SELECT id, question, answer, source FROM sent_answers WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return {"saved_at": _dt(row["saved_at"]), "cv": cv, "cover": row["cover"], "notes": row["notes"],
                "answers": [dict(a) for a in answers]}

    def set_sent_text(self, job_id: str, field: str, text: str) -> bool:
        if field not in ("cover", "notes"):
            raise ValueError(f"unknown field {field!r}")
        with self.conn:
            return self.conn.execute(f"UPDATE sent_records SET {field} = ? WHERE job_id = ?", (text, job_id)).rowcount > 0

    def add_sent_answer(self, job_id: str, question: str, answer: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO sent_answers (job_id, question, answer, source) VALUES (?,?,?,'manual')",
                              (job_id, question, answer))

    def set_sent_answer(self, answer_id: int, answer: str) -> bool:
        with self.conn:
            return self.conn.execute("UPDATE sent_answers SET answer = ?, source = CASE source WHEN 'draft' THEN 'form' ELSE source END"
                                     " WHERE id = ?", (answer, answer_id)).rowcount > 0

    def delete_sent_answer(self, answer_id: int) -> str | None:
        """The job the answer belonged to, or None when there was no such answer."""
        row = self.conn.execute("SELECT job_id FROM sent_answers WHERE id = ?", (answer_id,)).fetchone()
        if row:
            with self.conn:
                self.conn.execute("DELETE FROM sent_answers WHERE id = ?", (answer_id,))
        return row[0] if row else None

    def follow_ups_due(self, now: datetime) -> list[tuple[Job, datetime]]:
        rows = self.conn.execute(
            "SELECT job_id, next_follow_up_at FROM applications WHERE status = 'applied' AND next_follow_up_at <= ?"
            " ORDER BY next_follow_up_at",
            (_iso(now),),
        ).fetchall()
        return [(self.get_job(r["job_id"]), _dt(r["next_follow_up_at"])) for r in rows]

    # --- dashboard -----------------------------------------------------------
    def _latest_evaluation(self, job_id: str) -> Evaluation | None:
        row = self.conn.execute(
            "SELECT job_id, score, fit_reasons, gaps, model, prompt_version FROM evaluations"
            " WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return Evaluation(**dict(row)) if row else None

    def match(self, job_id: str) -> Match | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        evaluation = self._latest_evaluation(job_id) or Evaluation(
            job_id=job_id, score=0, fit_reasons="", gaps="", model="", prompt_version=""
        )
        return Match(job_id=job_id, job=job, evaluation=evaluation, draft=self.latest_draft(job_id), status=self.status(job_id) or "new")

    def dashboard_rows(self) -> list[dict]:
        """One row per scored job, with what the Dashboard page counts."""
        rows = self.conn.execute(
            "SELECT j.id, j.company, j.title, j.source, substr(j.first_seen_at, 1, 10) AS day,"
            " (SELECT e.score FROM evaluations e WHERE e.job_id = j.id ORDER BY e.id DESC LIMIT 1) AS score,"
            " coalesce(a.status, 'new') AS status, a.applied_at, p.status AS prep, p.reason"
            " FROM jobs j LEFT JOIN applications a ON a.job_id = j.id LEFT JOIN prepared_apps p ON p.job_id = j.id"
            " WHERE EXISTS (SELECT 1 FROM evaluations e WHERE e.job_id = j.id)"
        ).fetchall()
        return [dict(r) for r in rows]

    def open_job_ids(self, min_score: int) -> list[str]:
        """Scored jobs still waiting for a decision (new or shortlisted), best score first, then newest."""
        rows = self.conn.execute(
            "SELECT j.id FROM jobs j JOIN applications a ON a.job_id = j.id"
            " JOIN (SELECT job_id, score FROM evaluations e WHERE e.id = (SELECT max(id) FROM evaluations WHERE job_id = e.job_id)) s"
            " ON s.job_id = j.id WHERE a.status IN ('new', 'shortlisted') AND s.score >= ?"
            " ORDER BY s.score DESC, j.first_seen_at DESC",
            (min_score,),
        ).fetchall()
        return [r[0] for r in rows]

    def applications_list(self) -> list[dict]:
        """Jobs the candidate applied to, newest application first."""
        rows = self.conn.execute(
            "SELECT j.id, j.company, j.title, j.url, a.status, a.applied_at, a.next_follow_up_at, p.status AS prep"
            " FROM applications a JOIN jobs j ON j.id = a.job_id LEFT JOIN prepared_apps p ON p.job_id = j.id"
            " WHERE a.applied_at IS NOT NULL OR a.status IN ('applied', 'interview', 'offer')"
            " ORDER BY coalesce(a.applied_at, a.updated_at) DESC"
        ).fetchall()
        return [dict(r) | {"applied_at": _dt(r["applied_at"]), "next_follow_up_at": _dt(r["next_follow_up_at"])} for r in rows]

    def latest_match_day(self) -> date | None:
        row = self.conn.execute(
            "SELECT max(substr(j.first_seen_at, 1, 10)) FROM jobs j WHERE EXISTS (SELECT 1 FROM evaluations e WHERE e.job_id = j.id)"
        ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def matches_for_day(self, day: date) -> list[Match]:
        rows = self.conn.execute(
            "SELECT j.id FROM jobs j WHERE substr(j.first_seen_at, 1, 10) = ?"
            " AND EXISTS (SELECT 1 FROM evaluations e WHERE e.job_id = j.id)",
            (day.isoformat(),),
        ).fetchall()
        matches = [self.match(r[0]) for r in rows]
        return sorted(matches, key=lambda m: m.evaluation.score, reverse=True)

    def pipeline(self) -> dict[str, list[Match]]:
        groups: dict[str, list[Match]] = {s: [] for s in PIPELINE_STATUSES}
        rows = self.conn.execute(
            f"SELECT job_id, status FROM applications WHERE status IN ({','.join('?' * len(PIPELINE_STATUSES))})"
            " ORDER BY updated_at DESC",
            PIPELINE_STATUSES,
        ).fetchall()
        for r in rows:
            groups[r["status"]].append(self.match(r["job_id"]))
        return groups

    def follow_up_due_ids(self, now: datetime) -> set[str]:
        return {job.id for job, _ in self.follow_ups_due(now)}

    def list_runs(self, limit: int = 30) -> list[tuple[int, Run]]:
        ids = [r[0] for r in self.conn.execute("SELECT id FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))]
        return [(i, self.get_run(i)) for i in ids]

    # --- inbox ----------------------------------------------------------------
    def processed_mail_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT message_id FROM mail_events")}

    def tracked_applications(self, statuses: tuple[str, ...]) -> list[dict]:
        rows = self.conn.execute(
            "SELECT j.id AS job_id, j.company, j.title, a.status FROM applications a JOIN jobs j ON j.id = a.job_id"
            f" WHERE a.status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
        return [dict(r) for r in rows]

    def record_mail(self, *, message_id: str, sender: str, subject: str, received_at: datetime, job_id: str | None,
                    outcome: str, evidence: str, to_status: str | None, now: datetime) -> dict | None:
        """Remember an email once and apply the status it brings. None when it was already recorded."""
        before = self.conn.execute(
            "SELECT status, applied_at, next_follow_up_at FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO mail_events (message_id, job_id, outcome, sender, subject, received_at, evidence,"
                    " from_status, to_status, from_applied_at, from_follow_up_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (message_id, job_id, outcome, sender, subject, _iso(received_at), evidence,
                     before["status"] if before else None, to_status, before["applied_at"] if before else None,
                     before["next_follow_up_at"] if before else None, _iso(now)),
                )
        except sqlite3.IntegrityError:  # recorded by an earlier or concurrent check
            return None
        if to_status:  # an application confirmation dates the application from the email
            self.set_status(job_id, to_status, received_at if to_status == "applied" else now)
        return self.mail_event(message_id)

    def mail_event(self, message_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT e.*, j.company, j.title FROM mail_events e LEFT JOIN jobs j ON j.id = e.job_id WHERE message_id = ?",
            (message_id,)).fetchone()
        return self._mail_row(row) if row else None

    def _mail_row(self, row) -> dict:
        return dict(row) | {"received_at": _dt(row["received_at"]), "created_at": _dt(row["created_at"]),
                            "undone_at": _dt(row["undone_at"])}

    def mail_changes(self, limit: int = 10) -> list[dict]:
        """Status changes made from emails, newest first."""
        rows = self.conn.execute(
            "SELECT e.*, j.company, j.title FROM mail_events e JOIN jobs j ON j.id = e.job_id"
            " WHERE e.to_status IS NOT NULL ORDER BY e.created_at DESC, e.received_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._mail_row(r) for r in rows]

    def latest_mail_change(self, job_id: str) -> dict | None:
        """The email behind the job's current status, while that status still holds."""
        row = self.conn.execute(
            "SELECT e.*, j.company, j.title FROM mail_events e JOIN jobs j ON j.id = e.job_id"
            " JOIN applications a ON a.job_id = e.job_id"
            " WHERE e.job_id = ? AND e.to_status = a.status AND e.undone_at IS NULL ORDER BY e.created_at DESC LIMIT 1",
            (job_id,)).fetchone()
        return self._mail_row(row) if row else None

    def undo_mail_change(self, message_id: str, now: datetime) -> str | None:
        """Put the application back as it was before the email. The job id, or None when the status changed since."""
        row = self.conn.execute(
            "SELECT e.job_id, e.from_status, e.from_applied_at, e.from_follow_up_at FROM mail_events e"
            " JOIN applications a ON a.job_id = e.job_id"
            " WHERE e.message_id = ? AND e.undone_at IS NULL AND e.to_status = a.status", (message_id,)).fetchone()
        if row is None:
            return None
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET status = ?, applied_at = ?, next_follow_up_at = ?, updated_at = ? WHERE job_id = ?",
                (row["from_status"], row["from_applied_at"], row["from_follow_up_at"], _iso(now), row["job_id"]))
            self.conn.execute("UPDATE mail_events SET undone_at = ? WHERE message_id = ?", (_iso(now), message_id))
        return row["job_id"]

    def set_mail_check(self, now: datetime, error: str | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO mail_checks (id, checked_at, error) VALUES (1, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET checked_at = excluded.checked_at, error = excluded.error",
                (_iso(now), error))

    def mail_check(self) -> dict | None:
        row = self.conn.execute("SELECT checked_at, error FROM mail_checks WHERE id = 1").fetchone()
        return {"checked_at": _dt(row[0]), "error": row[1]} if row else None

    # --- companies to check ---------------------------------------------------
    def company_checks(self) -> dict[str, datetime]:
        return {r["name"]: _dt(r["checked_at"]) for r in self.conn.execute("SELECT name, checked_at FROM company_checks")}

    def mark_company_checked(self, name: str, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO company_checks (name, checked_at) VALUES (?, ?)"
                " ON CONFLICT(name) DO UPDATE SET checked_at = excluded.checked_at",
                (name, _iso(now)),
            )

    def unmark_company(self, name: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM company_checks WHERE name = ?", (name,))

    # --- runs ---------------------------------------------------------------
    def start_run(self, run: Run) -> int:
        with self.conn:
            cur = self.conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_iso(run.started_at),))
        return cur.lastrowid

    def finish_run(self, run_id: int, run: Run) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, fetched=?, new=?, kept=?, scored=?, drafted=?, failed_sources=?, error=?"
                " WHERE id = ?",
                (_iso(run.finished_at), run.fetched, run.new, run.kept, run.scored, run.drafted,
                 json.dumps(run.failed_sources), run.error, run_id),
            )

    def last_success_day(self) -> date | None:
        row = self.conn.execute(
            "SELECT started_at FROM runs WHERE finished_at IS NOT NULL AND error IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return _dt(row[0]).date() if row else None

    def last_attempt_at(self) -> datetime | None:
        row = self.conn.execute("SELECT started_at FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return _dt(row[0]) if row else None

    def get_run(self, run_id: int) -> Run | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        data = {k: row[k] for k in row.keys() if k != "id"}
        data["failed_sources"] = json.loads(data["failed_sources"] or "[]")
        return Run(**{k: v for k, v in data.items() if v is not None})
