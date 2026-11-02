"""One-off import of the files written by the earlier scheduled-prompt version."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from jobcopilot.db import DB
from jobcopilot.models import STATUSES, Job


def import_legacy(db: DB, home: Path) -> int:
    """Import seen.json and tracker.tsv from `home`. Returns how many new jobs were added."""
    now = datetime.now(timezone.utc)
    known = db.known_urls()
    jobs: dict[str, tuple[Job, str]] = {}

    seen_path = home / "seen.json"
    if seen_path.exists():
        for url in json.loads(seen_path.read_text() or "{}"):
            jobs[url] = (Job(url=url, source="legacy", company="", title=url), "skipped")

    tracker_path = home / "tracker.tsv"
    if tracker_path.exists():
        with tracker_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                url = (row.get("link") or "").strip()
                if not url:
                    continue
                status = (row.get("status") or "new").strip()
                job = Job(url=url, source="legacy", company=row.get("company", ""), title=row.get("role", ""))
                jobs[url] = (job, status if status in STATUSES else "new")

    added = [(j, s) for url, (j, s) in jobs.items() if url not in known]
    db.insert_jobs([j for j, _ in added], now)
    for job, status in added:
        if status != "new":
            db.set_status(job.id, status, now)
    return len(added)
