"""Long-running scheduler for containers: one successful run per day, at or after the scheduled time.

Checking every minute (instead of sleeping until 08:30) means a machine that was asleep or
off at 08:30 still gets its run as soon as it is back."""

from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta
from typing import Callable

RETRY_AFTER = timedelta(hours=1)
CHECK_EVERY_S = 60


def should_run(
    now: datetime, last_success_day: date | None, last_attempt_at: datetime | None, run_at: time
) -> bool:
    if now.time() < run_at or last_success_day == now.date():
        return False
    if last_attempt_at is not None and last_attempt_at.date() == now.date() and now - last_attempt_at < RETRY_AFTER:
        return False
    return True


def loop(run_once: Callable[[], None], state: Callable[[], tuple[date | None, datetime | None]], run_at: time) -> None:
    """Forever: check every minute whether today's run is due, and run it."""
    while True:
        last_success_day, last_attempt_at = state()
        if should_run(datetime.now().astimezone(), last_success_day, last_attempt_at, run_at):
            run_once()
        _time.sleep(CHECK_EVERY_S)
