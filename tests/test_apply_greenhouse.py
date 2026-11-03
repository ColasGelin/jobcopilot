import json
from pathlib import Path

from jobcopilot.apply.greenhouse import QUESTIONS_URL, greenhouse_ref, snapshot_from_greenhouse
from jobcopilot.config import WatchlistEntry

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_questions.json"


def test_greenhouse_ref_variants():
    wl = [WatchlistEntry(name="Acme", ats="greenhouse", slug="acme")]
    assert greenhouse_ref("https://job-boards.greenhouse.io/acme/jobs/123", "X", []) == ("acme", "123")
    assert greenhouse_ref("https://job-boards.eu.greenhouse.io/acme/jobs/123", "X", []) == ("acme", "123")
    assert greenhouse_ref("https://boards.greenhouse.io/acme/jobs/123?t=1", "X", []) == ("acme", "123")
    assert greenhouse_ref("https://acme.com/careers?gh_jid=123", "Acme", wl) == ("acme", "123")
    assert greenhouse_ref("https://acme.com/careers?gh_jid=123", "Other", wl) is None
    assert greenhouse_ref("https://jobs.lever.co/acme/1", "Acme", wl) is None
    assert QUESTIONS_URL.format(slug="acme", id="123").endswith("/boards/acme/jobs/123?questions=true")


def test_snapshot_from_greenhouse():
    snap = snapshot_from_greenhouse(json.loads(FIXTURE.read_text()), "https://u")
    kinds = {f.key: f.kind for f in snap.fields}
    assert kinds["first_name"] == "text" and kinds["resume"] == "file" and kinds["question_1"] == "textarea"
    assert kinds["question_3"] == "checkbox" and kinds["location"] == "text" and kinds["veteran_status"] == "select"
    assert "hidden_field" not in kinds and "latitude" not in kinds
    assert "resume_text" not in kinds  # the paste-instead box next to an upload is skipped
    sel = next(f for f in snap.fields if f.key == "question_2")
    assert sel.options == ["Yes", "No"] and sel.selector == "#question_2" and sel.required
    why = next(f for f in snap.fields if f.key == "question_1")
    assert why.description == "Great answers are 200-400 words. Please do not use AI to write this answer."
    assert "Build agents at Acme." in snap.page_text and "do not use AI" in snap.page_text
    assert snap.url == "https://u"


def test_compliance_questions_flagged_eeo():
    snap = snapshot_from_greenhouse(json.loads(FIXTURE.read_text()), "https://u")
    assert next(f for f in snap.fields if f.key == "veteran_status").eeo
    assert not next(f for f in snap.fields if f.key == "question_2").eeo
