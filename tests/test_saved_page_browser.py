import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from jobcopilot.apply.models import FormSnapshot  # noqa: E402
from jobcopilot.apply.saved_page import read_saved_page  # noqa: E402

FORMS = Path(__file__).parent / "fixtures" / "forms"


def test_read_saved_page_reads_form_without_running_or_loading_anything():
    # Its own module: sync Playwright can't start while another test module's browser is open.
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    html = ("<!-- saved from url=(0040)https://www.linkedin.com/jobs/view/123/ -->\n"
            + (FORMS / "plain.html").read_text()
            + '<script>document.body.innerHTML = "SCRIPT RAN"</script>'
            + f'<img src="{base}/img.png"><link rel="stylesheet" href="{base}/s.css"><iframe src="{base}/frame"></iframe>')
    try:
        page = read_saved_page(html)
    finally:
        server.shutdown()
    if not hasattr(page, "form") and "Executable doesn't exist" in page.reason:
        pytest.skip("Chromium not installed")
    assert page.url == "https://www.linkedin.com/jobs/view/123/"
    assert "SCRIPT RAN" not in page.text and "We build agents." in page.text
    assert isinstance(page.form, FormSnapshot) and len(page.form.fields) == 9
    assert page.form.url == "https://www.linkedin.com/jobs/view/123/"
    assert hits == []  # no image, stylesheet or frame was fetched
