from jobcopilot.apply.browser import classify
from jobcopilot.apply.models import FormSnapshot, NeedsYou


def raw(fields=(), text="", has_password=False, captcha=False):
    return {"fields": list(fields), "text": text, "has_password": has_password, "captcha": captcha, "form_count": 1}


F = {"key": "q", "selector": "#q", "label": "Why us?", "kind": "textarea", "options": [], "required": True, "max_length": None, "description": ""}


def test_classify_signin_wins_over_fields():
    assert classify([("https://a", raw([F], has_password=True))], "https://a") == NeedsYou(reason="Sign-in or account creation required")


def test_classify_captcha_without_fields():
    assert classify([("https://a", raw(captcha=True))], "https://a") == NeedsYou(reason="CAPTCHA or bot check on the page")


def test_classify_captcha_beside_a_form_still_reads_the_form():
    snap = classify([("https://a", raw([F], captcha=True))], "https://a")
    assert isinstance(snap, FormSnapshot) and snap.fields[0].key == "q"


def test_classify_no_fields():
    assert classify([("https://a", raw(text="About"))], "https://a") == NeedsYou(reason="No application form found")


def test_classify_fields_from_child_frame():
    snap = classify([("https://a", raw(text="Job")), ("https://gh/embed", raw([F], text="Form"))], "https://a")
    assert snap.fields[0].frame == "https://gh/embed" and "Job" in snap.page_text and "Form" in snap.page_text
    assert snap.url == "https://a"


def test_classify_main_frame_fields_have_empty_frame_and_text_is_capped():
    snap = classify([("https://a", raw([F], text="x" * 30_000))], "https://a")
    assert snap.fields[0].frame == "" and len(snap.page_text) == 20_000
