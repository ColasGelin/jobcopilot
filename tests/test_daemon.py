from datetime import date, datetime, time, timedelta

from jobcopilot.daemon import should_run

AT = time(8, 30)
DAY = date(2026, 11, 3)


def at(h, m, day=DAY):
    return datetime.combine(day, time(h, m))


def test_waits_before_scheduled_time():
    assert not should_run(at(8, 29), last_success_day=None, last_attempt_at=None, run_at=AT)


def test_runs_at_scheduled_time():
    assert should_run(at(8, 30), last_success_day=None, last_attempt_at=None, run_at=AT)


def test_catches_up_later_in_the_day():
    assert should_run(at(15, 0), last_success_day=DAY - timedelta(days=1), last_attempt_at=None, run_at=AT)


def test_runs_once_per_day():
    assert not should_run(at(9, 0), last_success_day=DAY, last_attempt_at=at(8, 30), run_at=AT)


def test_failed_attempt_waits_an_hour_before_retrying():
    assert not should_run(at(9, 0), last_success_day=None, last_attempt_at=at(8, 30), run_at=AT)
    assert should_run(at(9, 31), last_success_day=None, last_attempt_at=at(8, 30), run_at=AT)


def test_yesterdays_failure_does_not_block_today():
    assert should_run(at(8, 30), last_success_day=None, last_attempt_at=at(23, 50, DAY - timedelta(days=1)), run_at=AT)
