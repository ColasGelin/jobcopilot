from jobcopilot.apply.saved_page import page_url


def test_page_url_from_the_browsers_saved_from_comment():
    html = '<!DOCTYPE html>\n<!-- saved from url=(0041)https://www.linkedin.com/jobs/view/4242/ -->\n<html></html>'
    assert page_url(html) == "https://www.linkedin.com/jobs/view/4242/"


def test_page_url_from_canonical_or_og_url_in_either_attribute_order():
    assert page_url('<link rel="canonical" href="https://acme.com/jobs/1?a=1&amp;b=2">') == "https://acme.com/jobs/1?a=1&b=2"
    assert page_url('<link href="https://acme.com/jobs/2" rel="canonical">') == "https://acme.com/jobs/2"
    assert page_url('<meta property="og:url" content="https://acme.com/jobs/3">') == "https://acme.com/jobs/3"
    assert page_url('<meta content="https://acme.com/jobs/4" property="og:url">') == "https://acme.com/jobs/4"


def test_page_url_ignores_local_and_missing_addresses():
    assert page_url("<!-- saved from url=(0022)file:///Users/x/a.html -->") == ""
    assert page_url('<link rel="canonical" href="/jobs/1">') == ""
    assert page_url("<html><body>No address</body></html>") == ""
