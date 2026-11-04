from jobcopilot.apply.browser import classify
from jobcopilot.apply.models import FormSnapshot, NeedsYou


def raw(fields=(), text="", has_password=False, captcha=False):
    return {"fields": list(fields), "text": text, "has_password": has_password, "captcha": captcha, "form_count": 1}


F = {"key": "q", "selector": "#q", "label": "Full name", "kind": "text", "options": [], "required": True, "max_length": None, "description": ""}


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


def test_comment_box_and_search_are_not_an_application_form():
    # A Hacker News post: a reply textarea and a search box, nothing that asks for a candidate.
    junk = [dict(F, key="text", label="robz75 1 day ago | parent | on: Ask HN: Who is hiring?", kind="textarea", required=False),
            dict(F, key="q", label="Guidelines | FAQ | Search:", kind="text", required=False)]
    assert classify([("https://news.ycombinator.com/item?id=1", raw(junk))], "https://x") == NeedsYou(reason="No application form found")


def test_one_applicant_field_is_enough():
    fields = [dict(F, key="e", label="Email", kind="email"), dict(F, key="m", label="Anything else?", kind="textarea")]
    assert isinstance(classify([("https://a", raw(fields))], "https://a"), FormSnapshot)


def test_a_long_post_used_as_a_label_does_not_count_as_applicant():
    post = "robz75 1 day ago | parent | on: Ask HN: Who is hiring? Evaboot | Agentic Python Engineer | REMOTE " + "x " * 60 + "LinkedIn Sales Navigator"
    snap = classify([("https://news.ycombinator.com/item?id=1", raw([dict(F, key="text", label=post, kind="textarea")]))], "https://x")
    assert snap == NeedsYou(reason="No application form found")
