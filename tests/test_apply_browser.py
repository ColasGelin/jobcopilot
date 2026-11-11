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
    assert [f.label for f in snap.fields] == ["Full name", "Gender", "Resume/CV"]


def test_never_clicks_a_submit_named_apply(page):
    # Review C1: an "Apply" submit button or a field-less step's "Postuler" button must never be clicked.
    read_page(page, url("submit_apply.html").replace("submit_apply", "review_step"))
    assert page.evaluate("window.sub") is False
    from jobcopilot.apply.browser import _click_apply
    page.goto(url("submit_apply.html"))
    assert _click_apply(page) is False and page.evaluate("window.sub") is False


@pytest.mark.parametrize("name,labels", [
    ("apply_position.html", ["Email"]),  # a longer label, and a link to another page
    ("apply_overlay.html", ["Full name"]),  # a cookie banner covers the button
    ("apply_new_tab.html", ["Email"]),  # the form opens in a new tab
    ("apply_late.html", ["Why us?", "Email"]),  # the form arrives in an iframe seconds after the click
])
def test_reveals_forms_behind_an_apply_button(page, name, labels):
    snap = read_page(page, url(name))
    assert isinstance(snap, FormSnapshot), snap
    assert sorted(f.label for f in snap.fields) == sorted(labels)
    assert len(page.context.pages) == 1  # a new tab is closed once read


def test_never_clicks_sign_in_or_send_buttons(page):
    assert read_page(page, url("apply_not_reveal.html")) == NeedsYou(reason="No application form found")
    assert page.evaluate("window.clicked") == []


@pytest.mark.parametrize("text,expected", [
    ("Apply", True), ("Apply for Position", True), ("  Apply\n now ", True), ("Aplicar a esta oferta", True),
    ("Postuler à cette offre", True), ("I'm interested", True), ("Apply with LinkedIn", False), ("Apply via Indeed", False),
    ("Enviar solicitud", False), ("Submit application", False), ("Applying tips for juniors and how to write a CV that stands out", False),
    ("Read more", False),
])
def test_reveal_labels(text, expected):
    from jobcopilot.apply.browser import _is_reveal
    assert _is_reveal(text) is expected


def test_goes_to_the_platform_form_page_when_the_job_page_has_none(page, monkeypatch):
    from jobcopilot.apply import browser
    seen = []
    monkeypatch.setattr(browser, "platform_form_url", lambda u, html: seen.append(u) or url("apply_target.html"))
    snap = read_page(page, url("noform.html"))
    assert isinstance(snap, FormSnapshot) and [f.label for f in snap.fields] == ["Email"]
    assert snap.url.endswith("apply_target.html") and seen == [url("noform.html")]


def test_the_candidates_window_is_never_navigated(page, monkeypatch):
    from jobcopilot.apply import browser
    monkeypatch.setattr(browser, "platform_form_url", lambda u, html: url("apply_target.html"))
    page.goto(url("noform.html"))
    assert read_page(page, "", click_apply=False) == NeedsYou(reason="No application form found")
    assert page.url.endswith("noform.html")
