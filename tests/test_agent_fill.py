from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from jobcopilot.agent.fill import fill_fields  # noqa: E402

FORMS = Path(__file__).parent / "fixtures" / "forms"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"Chromium unavailable: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    pg.goto((FORMS / "fill.html").as_uri())
    pg.wait_for_load_state("load")
    yield pg
    pg.close()


def field(key, selector, label, kind, value, answer_kind="open", frame=""):
    return {"key": key, "selector": selector, "label": label, "kind": kind, "value": value, "answer_kind": answer_kind,
            "frame": frame, "options": []}


FIELDS = [
    field("fname", "#fname", "First name", "text", "Alex", "fact"),
    field("email", "#email", "Email", "email", "alex@example.com", "fact"),
    field("why", "#why", "Why us?", "textarea", "Because I build agents."),
    field("visa", "#visa", "Visa", "select", "No"),
    field("reloc", '[name="reloc"]', "Relocate?", "radio", "Yes"),
    field("langs", '[name="langs"]', "Languages", "checkbox", "Spanish"),
    field("cv", "#cv", "Resume/CV", "file", "CV", "cv"),
]


def test_fills_every_kind_and_never_submits(page, tmp_path):
    cv = tmp_path / "Alex-Martin-CV.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    r = fill_fields(page, FIELDS, cv)
    assert set(r.filled) == {f["key"] for f in FIELDS} and r.unfilled == []
    assert page.input_value("#fname") == "Alex" and page.input_value("#why") == "Because I build agents."
    assert page.input_value("#visa") == "0"
    assert page.is_checked('[name="reloc"][value="y"]') and page.is_checked('[name="langs"][value="es"]')
    assert not page.is_checked('[name="langs"][value="en"]')
    assert page.evaluate("document.getElementById('cv').files[0].name") == "Alex-Martin-CV.pdf"
    assert page.evaluate("window.submitted") is False and page.evaluate("window.clicked") == []


def test_fills_inside_iframe(page):
    frame_url = (FORMS / "fill_iframe.html").as_uri()
    r = fill_fields(page, [field("q", "#q", "Portfolio", "url", "https://example.com", "fact", frame=frame_url)], None)
    assert r.filled == ["q"]
    assert page.frame(url=frame_url).input_value("#q") == "https://example.com"


def test_label_fallback_when_selector_changed(page):
    r = fill_fields(page, [field("phone", "#phone_old", "Phone", "tel", "+34 600", "fact")], None)
    assert r.filled == ["phone"] and page.input_value("#renamed") == "+34 600"


def test_missing_field_and_cv_reported_unfilled(page):
    r = fill_fields(page, [field("nope", "#nope", "Shoe size", "text", "42"), field("cv", "#cv", "Resume/CV", "file", "CV", "cv"),
                           field("visa", "#visa", "Visa", "select", "Maybe")], None)
    assert r.filled == []
    whys = {u["key"]: u["why"] for u in r.unfilled}
    assert set(whys) == {"nope", "cv", "visa"} and "not found" in whys["nope"] and "CV" in whys["cv"]
    assert all(u["label"] for u in r.unfilled)


def test_reveal_form_never_submits(browser):
    from jobcopilot.agent.fill import reveal_form

    pg = browser.new_page()
    pg.goto((FORMS / "submit_apply.html").as_uri())
    reveal_form(pg, [field("x", "#gone", "Gone", "text", "v")])
    assert pg.evaluate("window.sub") is False
    pg.close()


def test_option_labels_normalised_and_iframe_found_by_search(browser):
    pg = browser.new_page()
    pg.goto((FORMS / "radio_star.html").as_uri())
    pg.wait_for_load_state("load")
    r = fill_fields(pg, [field("r", '[name="r"]', "Relocate?", "radio", "Yes"),
                         # recorded without a frame (Greenhouse API) but the page embeds the form in an iframe
                         field("q", "#q", "Why us?", "textarea", "Because.")], None)
    assert r.filled == ["r", "q"], r.unfilled
    assert pg.is_checked('[name="r"][value="y"]')
    r = fill_fields(pg, [field("r", '[name="r"]', "Relocate?", "radio", "No")], None)
    assert r.filled == ["r"]
    pg.close()
