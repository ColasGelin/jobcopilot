from jobcopilot.text import html_to_text, normalize


def test_html_to_text():
    assert html_to_text("&lt;p&gt;Hello&lt;/p&gt;<b> world</b>\n\n!") == "Hello world !"


def test_normalize_strips_accents():
    assert normalize("  Málaga  SPAIN ") == "malaga spain"


def test_html_to_text_drops_unclosed_trailing_tag():
    assert html_to_text('Hello <p class="x') == "Hello"
