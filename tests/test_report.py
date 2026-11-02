from datetime import date, datetime, timezone

from conftest import make_job
from jobcopilot.models import Draft, Evaluation, Run
from jobcopilot.report import render_report, write_report

DAY = date(2026, 11, 2)
RUN = Run(started_at=datetime(2026, 11, 2, 8, 30, tzinfo=timezone.utc), fetched=40, new=12, kept=5, scored=5, drafted=2)


def ev(job, score):
    return Evaluation(job_id=job.id, score=score, fit_reasons="fits", gaps="gap", model="m", prompt_version="v")


def dr(job):
    return Draft(job_id=job.id, cv_tweaks=["tweak one"], cover_note="COVER", outreach_message="HELLO", contact_hint="CTO", model="m", prompt_version="v")


def test_report_sorted_by_score():
    a, b = make_job(1, company="LowCo"), make_job(2, company="HighCo")
    md = render_report(DAY, RUN, [(a, ev(a, 61), dr(a)), (b, ev(b, 90), None)], [])
    assert md.startswith("# Job matches — 2026-11-02")
    assert md.index("HighCo") < md.index("LowCo")
    assert "COVER" in md and "HELLO" in md and "tweak one" in md
    assert "Draft failed" in md


def test_report_summary_counts_and_failed_sources():
    md = render_report(DAY, RUN.model_copy(update={"failed_sources": ["hn", "remoteok"]}), [], [])
    assert "40 fetched" in md and "12 new" in md
    assert "Failed sources: hn, remoteok" in md


def test_report_credits_remote_ok():
    j = make_job(1, source="remoteok")
    assert "Remote OK" in render_report(DAY, RUN, [(j, ev(j, 70), None)], [])


def test_report_lists_follow_ups():
    j = make_job(1, company="FollowCo", title="Backend Dev")
    md = render_report(DAY, RUN, [], [(j, datetime(2026, 10, 30, tzinfo=timezone.utc))])
    assert "FollowCo — Backend Dev" in md


def test_report_without_follow_ups():
    assert "No follow-ups due" in render_report(DAY, RUN, [], [])


def test_write_report_overwrites(tmp_path):
    write_report(tmp_path, DAY, "first")
    path = write_report(tmp_path, DAY, "second")
    assert path == tmp_path / "2026-11-02.md"
    assert path.read_text() == "second"
