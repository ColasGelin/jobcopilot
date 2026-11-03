from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from jobcopilot.apply.browser import read_page  # noqa: E402
from jobcopilot.apply.models import FormSnapshot, NeedsYou  # noqa: E402

FORMS = Path(__file__).parent / "fixtures" / "forms"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:  # Chromium not installed
            pytest.skip(f"Chromium unavailable: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def url(name: str) -> str:
    return (FORMS / name).as_uri()


def test_reads_every_kind(page):
    snap = read_page(page, url("plain.html"))
    assert isinstance(snap, FormSnapshot)
    by_label = {f.label: f for f in snap.fields}
    assert by_label["First name"].kind == "text" and by_label["First name"].required and by_label["First name"].selector == "#fname"
    assert by_label["Email address"].kind == "email"
    assert by_label["Phone number"].kind == "tel"
    assert by_label["LinkedIn URL"].kind == "url"
    assert by_label["Why do you want to join?"].max_length == 500
    assert by_label["Visa sponsorship"].options == ["Yes", "No"]
    reloc = by_label["Willing to relocate?"]
    assert reloc.kind == "radio" and reloc.options == ["Yes", "No"]
    assert by_label["I agree to the privacy policy"].kind == "checkbox"
    assert by_label["Resume/CV"].kind == "file"  # visually hidden file inputs still count
    assert len(snap.fields) == 9  # hidden and display:none inputs skipped
    assert "We build agents." in snap.page_text


def test_clicks_apply_to_reveal_form(page):
    snap = read_page(page, url("apply_button.html"))
    assert isinstance(snap, FormSnapshot) and [f.label for f in snap.fields] == ["Full name"]


def test_reads_iframe_form(page):
    snap = read_page(page, url("iframe_host.html"))
    assert isinstance(snap, FormSnapshot)
    assert {f.label for f in snap.fields} == {"Why us?", "Email"}
    assert all(f.frame.endswith("iframe_form.html") for f in snap.fields)
    assert "Join us." in snap.page_text


def test_signin_captcha_and_noform_pages(page):
    assert read_page(page, url("signin.html")) == NeedsYou(reason="Sign-in or account creation required")
    assert read_page(page, url("captcha.html")) == NeedsYou(reason="CAPTCHA or bot check on the page")
    assert read_page(page, url("noform.html")) == NeedsYou(reason="No application form found")


def test_reads_current_page_without_navigating(page):
    page.goto(url("plain.html"))
    snap = read_page(page, "")
    assert isinstance(snap, FormSnapshot) and snap.url.endswith("plain.html")



def test_wrapping_labels_are_cleaned(page):
    snap = read_page(page, url("wrapped_labels.html"))
    assert [f.label for f in snap.fields] == ["Full name", "Gender"]
