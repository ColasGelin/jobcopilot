from pathlib import Path

import pytest

pytest.importorskip("playwright")

from jobcopilot.apply.models import FormSnapshot  # noqa: E402
from jobcopilot.apply.reader import read_in_browser  # noqa: E402

FORMS = Path(__file__).parent / "fixtures" / "forms"


def test_read_in_browser_headless():
    # Its own module: sync Playwright can't start while another test module's browser is open.
    snap = read_in_browser((FORMS / "plain.html").as_uri())
    if not isinstance(snap, FormSnapshot) and "Executable doesn't exist" in snap.reason:
        pytest.skip("Chromium not installed")
    assert isinstance(snap, FormSnapshot) and len(snap.fields) == 9
    assert read_in_browser((FORMS / "missing.html").as_uri()).reason.startswith("Page failed to load")
