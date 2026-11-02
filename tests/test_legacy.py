import json
from datetime import datetime, timezone

from jobcopilot.db import DB
from jobcopilot.legacy import import_legacy


def test_import_legacy_reads_tracker_and_seen(tmp_path):
    (tmp_path / "seen.json").write_text(json.dumps({"https://a/1": "2026-10-29", "https://b/2": "2026-10-29"}))
    (tmp_path / "tracker.tsv").write_text(
        "date\tcompany\trole\tscore\tlink\tstatus\n2026-10-29\tAcme\tAI Engineer\t80\thttps://b/2\tapplied\n"
    )
    db = DB(":memory:")
    assert import_legacy(db, tmp_path) == 2
    assert db.known_urls() == {"https://a/1", "https://b/2"}
    from jobcopilot.models import Job
    assert db.status(Job(url="https://b/2", source="", company="", title="").id) == "applied"
    assert import_legacy(db, tmp_path) == 0  # idempotent


def test_import_legacy_missing_files_returns_zero(tmp_path):
    assert import_legacy(DB(":memory:"), tmp_path) == 0
