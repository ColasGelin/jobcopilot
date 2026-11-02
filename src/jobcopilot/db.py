"""SQLite storage for jobs, evaluations, drafts, applications and runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from jobcopilot.models import STATUSES, Draft, Evaluation, Job, Match, Run
from jobcopilot.text import normalize

FOLLOW_UP_AFTER = timedelta(days=5)
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
            for table, col in (("drafts", "job_id"), ("evaluations", "job_id"), ("applications", "job_id"), ("jobs", "id")):
                self.conn.executemany(f"DELETE FROM {table} WHERE {col} = ?", [(i,) for i in job_ids])

    def get_job(self, job_id: str) -> Job | None:
        row = self.conn.execute(f"SELECT {_JOB_COLS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["remote"] = bool(data["remote"])
        data["posted_at"] = _dt(data["posted_at"])
        return Job(**data)

    def known_urls(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT url FROM jobs")}

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
