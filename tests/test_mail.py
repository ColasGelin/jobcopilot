from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from conftest import make_job
from jobcopilot import mail as mail_mod
from jobcopilot.daemon import periodic_due
from jobcopilot.db import DB
from jobcopilot.llm import FakeLLMClient
from jobcopilot.mail import Mail, check_mail, fetch, moves_forward, parse, related
from jobcopilot.models import Evaluation
from jobcopilot.web import create_app

NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 11, 10, 9, 30, tzinfo=timezone.utc)


def mail(i: int, subject: str, body: str, sender: str = "Talent <jobs@ats.example>") -> Mail:
    return Mail(message_id=f"<m{i}@x>", sender=sender, subject=subject, received_at=RECEIVED, body=body)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "jobs.db"


@pytest.fixture
def jobs(db_path):
    """Acme (applied), Globex (interview), Initech (shortlisted), Umbrella (new: not tracked)."""
    db = DB(db_path)
    acme, globex = make_job(1, company="Acme"), make_job(2, company="Globex")
    initech, umbrella = make_job(3, company="Initech"), make_job(4, company="Umbrella")
    db.insert_jobs([acme, globex, initech, umbrella], NOW - timedelta(days=5))
    for j in (acme, globex, initech, umbrella):
        db.save_evaluation(Evaluation(job_id=j.id, score=80, fit_reasons="f", gaps="g", model="m", prompt_version="v"))
    db.set_status(acme.id, "applied", NOW - timedelta(days=3))
    db.set_status(globex.id, "applied", NOW - timedelta(days=4))
    db.set_status(globex.id, "interview", NOW - timedelta(days=1))
    db.set_status(initech.id, "shortlisted", NOW - timedelta(days=2))
    return {"acme": acme, "globex": globex, "initech": initech, "umbrella": umbrella}


def answer(*results):
    return {"results": [dict(message_id=m, job_id=j, outcome=o, evidence=e) for m, j, o, e in results]}


# --- parsing --------------------------------------------------------------------
def test_parse_prefers_plain_text_and_decodes_headers():
    msg = EmailMessage()
    msg["Message-ID"], msg["From"], msg["Date"] = "<a@b>", "=?utf-8?q?Jos=C3=A9?= <hr@acme.com>", "Tue, 10 Nov 2026 09:30:00 +0000"
    msg["Subject"] = "=?utf-8?q?Votre_candidature_=C3=A0_Acme?="
    msg.set_content("Unfortunately   we will not move forward.")
    msg.add_alternative("<p>HTML version</p>", subtype="html")
    m = parse(msg.as_bytes(), NOW)
    assert (m.message_id, m.sender, m.subject) == ("<a@b>", "José <hr@acme.com>", "Votre candidature à Acme")
    assert m.body == "Unfortunately we will not move forward." and m.received_at == RECEIVED


def test_parse_html_only_and_bad_date():
    msg = EmailMessage()
    msg["Message-ID"], msg["Date"] = "<a@b>", "not a date"
    msg.set_content("<p>Hello&nbsp;<b>there</b></p>", subtype="html")
    m = parse(msg.as_bytes(), NOW)
    assert m.body == "Hello there" and m.received_at == NOW


# --- which emails reach Claude ------------------------------------------------------
def test_only_emails_naming_a_tracked_company_are_related():
    candidates = [{"job_id": "a", "company": "Acme", "title": "t", "status": "applied"},
                  {"job_id": "x", "company": "AI", "title": "t", "status": "applied"}]  # too short to match on
    mails = [mail(1, "Your application to ACME", ""), mail(2, "Family photos", "acmecorp AI stuff"), mail(3, "News", "Ácme update")]
    assert [(m.message_id, [c["job_id"] for c in cs]) for m, cs in related(mails, candidates)] == [("<m1@x>", ["a"]), ("<m3@x>", ["a"])]


@pytest.mark.parametrize("current,outcome,expected", [
    ("applied", "rejected", True), ("interview", "rejected", True), ("shortlisted", "applied", True),
    ("applied", "interview", True), ("interview", "offer", True), ("interview", "interview", False),
    ("interview", "applied", False), ("offer", "rejected", False), ("rejected", "interview", False),
    ("applied", "none", False), ("applied", "hired", False),
])
def test_moves_only_forward(current, outcome, expected):
    assert moves_forward(current, outcome) is expected


# --- a check ----------------------------------------------------------------------------
def test_check_updates_statuses_and_records_each_email_once(db_path, jobs):
    db, acme, globex, initech = DB(db_path), jobs["acme"], jobs["globex"], jobs["initech"]
    mails = [
        mail(1, "Acme: update on your application", "We will not move forward."),
        mail(2, "Globex interview", "Thanks for the call."),  # already at interview: no change
        mail(3, "Thanks for applying to Initech", "We received your application."),
        mail(4, "Umbrella newsletter", "Umbrella is hiring"),  # not tracked: never sent to Claude
        mail(5, "Acme", "Hello"),
    ]
    llm = FakeLLMClient([answer(("<m1@x>", acme.id, "rejected", "We will not move forward."),
                                ("<m2@x>", globex.id, "interview", ""),
                                ("<m3@x>", initech.id, "applied", "We received your application."),
                                ("<m5@x>", globex.id, "offer", "fake"))])  # Globex is not named in m5: ignored
    changes = check_mail(db, mails, llm, "haiku", NOW)

    assert "Umbrella" not in llm.calls[0][0]
    assert [(c["company"], c["from_status"], c["to_status"]) for c in changes] == [("Acme", "applied", "rejected"), ("Initech", "shortlisted", "applied")]
    assert (db.status(acme.id), db.status(globex.id), db.status(initech.id)) == ("rejected", "interview", "applied")
    applied_at = {r["id"]: r["applied_at"] for r in db.applications_list()}[initech.id]
    assert applied_at == RECEIVED  # dated from the confirmation email
    assert db.processed_mail_ids() == {"<m1@x>", "<m2@x>", "<m3@x>", "<m5@x>"}
    assert db.mail_event("<m5@x>")["job_id"] is None

    # the next check skips what was recorded, so Claude is not called again
    assert check_mail(db, [m for m in mails if m.message_id not in db.processed_mail_ids()], FakeLLMClient([]), "haiku", NOW) == []


def test_undo_restores_the_application_exactly(db_path, jobs):
    db, acme = DB(db_path), jobs["acme"]
    before = {r["id"]: r for r in db.applications_list()}[acme.id]
    check_mail(db, [mail(1, "Acme", "No.")], FakeLLMClient([answer(("<m1@x>", acme.id, "rejected", "No."))]), "haiku", NOW)
    assert db.latest_mail_change(acme.id)["message_id"] == "<m1@x>"

    assert db.undo_mail_change("<m1@x>", NOW) == acme.id
    after = {r["id"]: r for r in db.applications_list()}[acme.id]
    assert (after["status"], after["applied_at"], after["next_follow_up_at"]) == ("applied", before["applied_at"], before["next_follow_up_at"])
    assert db.latest_mail_change(acme.id) is None and db.undo_mail_change("<m1@x>", NOW) is None


def test_undo_refused_once_the_status_was_changed_by_hand(db_path, jobs):
    db, acme = DB(db_path), jobs["acme"]
    check_mail(db, [mail(1, "Acme", "No.")], FakeLLMClient([answer(("<m1@x>", acme.id, "rejected", "No."))]), "haiku", NOW)
    db.set_status(acme.id, "interview", NOW)
    assert db.undo_mail_change("<m1@x>", NOW) is None and db.status(acme.id) == "interview"


# --- IMAP -------------------------------------------------------------------------------
class FakeIMAP:
    instances = []

    def __init__(self, host):
        self.host, self.selected, self.fetched = host, None, []
        FakeIMAP.instances.append(self)
        msg = EmailMessage()
        msg["Message-ID"], msg["Subject"] = "<new@x>", "Acme"
        msg.set_content("hi")
        self.raw = {b"1": b"Message-ID: <old@x>\r\n\r\n", b"2": msg.as_bytes()}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.user = user

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)

    def search(self, charset, *criteria):
        self.criteria = criteria
        return "OK", [b"1 2"]

    def fetch(self, num, what):
        self.fetched.append(what)
        return "OK", [(b"", self.raw[num])]


def test_fetch_is_read_only_and_skips_known_emails(monkeypatch):
    monkeypatch.setattr(mail_mod.imaplib, "IMAP4_SSL", FakeIMAP)
    mails = fetch("imap.example", "me", "pw", "INBOX", date(2026, 11, 4), {"<old@x>"}, NOW)
    imap = FakeIMAP.instances[-1]
    assert [m.message_id for m in mails] == ["<new@x>"]
    assert imap.selected == ("INBOX", True) and imap.criteria == ("SINCE", "04-Nov-2026")
    assert all("PEEK" in f for f in imap.fetched)  # never marks an email as read


# --- every page shows it -------------------------------------------------------------------
def test_pages_show_email_changes_and_undo_works(db_path, jobs):
    db, acme = DB(db_path), jobs["acme"]
    check_mail(db, [mail(1, "Acme decision", "We will not move forward.")],
               FakeLLMClient([answer(("<m1@x>", acme.id, "rejected", "We will not move forward."))]), "haiku", NOW)
    db.set_mail_check(NOW, None)
    c = TestClient(create_app(db_path, clock=lambda: NOW), base_url="http://localhost", headers={"HX-Request": "true"})

    dash = c.get("/").text
    assert "From your inbox" in dash and "Acme decision" in dash and "Applied → Rejected" in dash
    assert "/mail/%3Cm1%40x%3E/undo" in dash
    for page in ("/pipeline", "/applied"):
        assert "Email · Rejected · 10 Nov" in c.get(page).text, page
    assert c.post("/mail/%3Cm1%40x%3E/undo", headers={"HX-Request": ""}).status_code == 403

    r = c.post("/mail/%3Cm1%40x%3E/undo")
    assert r.status_code == 200 and r.headers["HX-Refresh"] == "true" and db.status(acme.id) == "applied"
    assert "Email · Rejected" not in c.get("/pipeline").text and "Undone" in c.get("/").text
    assert c.post("/mail/%3Cm1%40x%3E/undo").status_code == 409


def test_dashboard_shows_a_failed_check(db_path, jobs):
    DB(db_path).set_mail_check(NOW, "IMAP4.error: AUTHENTICATIONFAILED")
    body = TestClient(create_app(db_path, clock=lambda: NOW), base_url="http://localhost").get("/").text
    assert "check failed 11 Nov, 18:00: IMAP4.error: AUTHENTICATIONFAILED" in body


# --- daemon ----------------------------------------------------------------------------------
def test_periodic_task_runs_at_start_then_every_period():
    every = timedelta(hours=1)
    assert periodic_due(NOW, None, every)
    assert not periodic_due(NOW, NOW - timedelta(minutes=59), every)
    assert periodic_due(NOW, NOW - every, every)
