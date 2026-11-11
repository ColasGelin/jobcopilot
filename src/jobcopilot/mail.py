"""Read the inbox and move tracked applications forward: received, interview, offer or rejected.

Only emails that name a company in the pipeline are sent to Claude; the rest of the inbox never leaves the server.
The mailbox is opened read-only, so nothing is marked as read."""

from __future__ import annotations

import email
import imaplib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from jobcopilot.db import DB
from jobcopilot.llm import LLMClient
from jobcopilot.scoring import load_prompt
from jobcopilot.text import html_to_text, normalize

MAIL_PROMPT_VERSION = "mail_v1"
BODY_LIMIT = 3000
MAX_PER_CHECK = 20  # emails sent to Claude per check; the rest wait for the next one
TRACKED = ("shortlisted", "applied", "interview")  # statuses an email can still move forward
RANK = {"shortlisted": 0, "applied": 1, "interview": 2, "offer": 3}
OUTCOMES = ("applied", "interview", "offer", "rejected", "none")
MIN_NAME = 3  # shorter company names match too much text

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "job_id": {"type": ["string", "null"]},
                    "outcome": {"enum": list(OUTCOMES)},
                    "evidence": {"type": "string"},
                },
                "required": ["message_id", "job_id", "outcome", "evidence"],
            },
        }
    },
    "required": ["results"],
}


@dataclass
class Mail:
    message_id: str
    sender: str
    subject: str
    received_at: datetime
    body: str


def _header(msg: Message, name: str) -> str:
    raw = msg.get(name)
    return str(make_header(decode_header(raw))) if raw else ""


def _body(msg: Message) -> str:
    plain, html = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if part.get_content_subtype() == "plain" else html).append(text)
    text = " ".join(plain) if plain else html_to_text(" ".join(html))
    return re.sub(r"\s+", " ", text).strip()[:BODY_LIMIT]


def parse(raw: bytes, fallback: datetime) -> Mail:
    msg = email.message_from_bytes(raw)
    try:
        received = parsedate_to_datetime(msg["Date"]) if msg["Date"] else fallback
    except (TypeError, ValueError):
        received = fallback
    name, addr = parseaddr(_header(msg, "From"))
    return Mail(
        message_id=(msg.get("Message-ID") or "").strip(),
        sender=f"{name} <{addr}>" if name else addr,
        subject=_header(msg, "Subject"),
        received_at=received,
        body=_body(msg),
    )


def fetch(host: str, user: str, password: str, folder: str, since: date, skip: set[str], now: datetime) -> list[Mail]:
    """Emails received since `since`, except those whose Message-ID is in `skip`. Read-only."""
    mails = []
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select(folder, readonly=True)
        _, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        for num in data[0].split():
            _, head = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            message_id = email.message_from_bytes(head[0][1]).get("Message-ID", "").strip()
            if not message_id or message_id in skip:
                continue
            _, full = imap.fetch(num, "(BODY.PEEK[])")
            mails.append(parse(full[0][1], now))
    return mails


def _mentions(name: str, text: str) -> bool:
    key = normalize(name)
    return len(key) >= MIN_NAME and re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text) is not None


def related(mails: list[Mail], candidates: list[dict]) -> list[tuple[Mail, list[dict]]]:
    """Each email that names a tracked company, with the applications it may be about."""
    out = []
    for m in mails:
        text = normalize(f"{m.sender} {m.subject} {m.body}")
        jobs = [c for c in candidates if _mentions(c["company"], text)]
        if jobs:
            out.append((m, jobs))
    return out


def _render(batch: list[tuple[Mail, list[dict]]]) -> str:
    jobs = {c["job_id"]: c for _, cs in batch for c in cs}
    payload = [
        {"message_id": m.message_id, "from": m.sender, "subject": m.subject, "date": m.received_at.date().isoformat(),
         "body": m.body}
        for m, _ in batch
    ]
    applications = [{"job_id": c["job_id"], "company": c["company"], "title": c["title"], "status": c["status"]}
                    for c in jobs.values()]
    return (load_prompt(MAIL_PROMPT_VERSION)
            .replace("{applications}", json.dumps(applications, ensure_ascii=False, indent=1))
            .replace("{emails}", json.dumps(payload, ensure_ascii=False, indent=1)))


def moves_forward(current: str, outcome: str) -> bool:
    if current not in TRACKED or outcome not in OUTCOMES or outcome == "none":
        return False
    return outcome == "rejected" or RANK[outcome] > RANK[current]


def check_mail(db: DB, mails: list[Mail], llm: LLMClient, model: str, now: datetime) -> list[dict]:
    """Classify the new emails about tracked applications and apply the status changes. Returns the changes."""
    candidates = db.tracked_applications(TRACKED)
    batch = related(mails, candidates)[:MAX_PER_CHECK]
    if not batch:
        return []
    allowed = {m.message_id: {c["job_id"] for c in cs} for m, cs in batch}
    by_id = {m.message_id: m for m, _ in batch}
    results = {r.get("message_id"): r for r in llm.complete_json(_render(batch), SCHEMA, model)["results"]}
    changes = []
    for message_id, mail in by_id.items():
        r = results.get(message_id) or {}
        job_id, outcome = r.get("job_id"), r.get("outcome", "none")
        if job_id not in allowed[message_id] or outcome not in OUTCOMES:  # only an application the email names
            job_id, outcome = None, "none"
        current = db.status(job_id) if job_id else None
        to_status = outcome if current and moves_forward(current, outcome) else None
        event = db.record_mail(
            message_id=mail.message_id, sender=mail.sender, subject=mail.subject, received_at=mail.received_at,
            job_id=job_id, outcome=outcome, evidence=str(r.get("evidence", ""))[:300], to_status=to_status, now=now,
        )
        if event and to_status:
            changes.append(event)
    return changes
