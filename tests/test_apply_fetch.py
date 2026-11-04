import json
from pathlib import Path

import httpx

from jobcopilot.apply.fetch import fetch_job
from jobcopilot.apply.models import NeedsYou
from jobcopilot.config import WatchlistEntry
from jobcopilot.llm import FakeLLMClient, LLMError

FIXTURES = Path(__file__).parent / "fixtures"
LEVER = json.loads((FIXTURES / "lever.json").read_text())[0]
ASHBY = json.loads((FIXTURES / "ashby.json").read_text())
GREENHOUSE = json.loads((FIXTURES / "greenhouse.json").read_text())["jobs"][0]


def client(routes: dict):
    def handler(request):
        for prefix, body in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=body) if body is not None else httpx.Response(404)
        raise AssertionError(f"unexpected request {request.url}")
    return httpx.Client(transport=httpx.MockTransport(handler))


def no_page(url):
    raise AssertionError("no browser expected")


def test_lever_link():
    url = LEVER["hostedUrl"]
    http = client({"https://api.lever.co/v0/postings/initech/22cbcc27": LEVER})
    job = fetch_job(url, [WatchlistEntry(name="Initech", ats="lever", slug="initech")], http, FakeLLMClient([]), "haiku", no_page)
    assert job.url == url and job.company == "Initech" and job.title == LEVER["text"] and job.source == "manual"
    assert job.description


def test_ashby_link_found_on_board():
    first = ASHBY["jobs"][0]
    http = client({"https://api.ashbyhq.com/posting-api/job-board/hooli": ASHBY})
    job = fetch_job(first["jobUrl"], [], http, FakeLLMClient([]), "haiku", no_page)
    assert job.title == first["title"] and job.company == "Hooli" and job.source == "manual"


def test_greenhouse_link_uses_company_name():
    url = GREENHOUSE["absolute_url"]
    http = client({"https://boards-api.greenhouse.io/v1/boards/globex/jobs/5020326101": GREENHOUSE})
    job = fetch_job(url, [], http, FakeLLMClient([]), "haiku", no_page)
    assert job.company == "Globex" and job.title == GREENHOUSE["title"] and job.url == url


def test_closed_ats_job_falls_back_to_the_page():
    url = LEVER["hostedUrl"]
    http = client({"https://api.lever.co/": None})
    llm = FakeLLMClient([{"company": "Initech", "title": "Engineer", "location": "Madrid", "remote": False, "salary_text": ""}])
    job = fetch_job(url, [], http, llm, "haiku", lambda u: ("Initech - Engineer", "Join us in Madrid. " * 15))
    assert job.title == "Engineer" and "Join us in Madrid." in job.description


def test_any_page_uses_page_text_and_claude_for_names_only():
    llm = FakeLLMClient([{"company": "Acme", "title": "AI Engineer", "location": "Remote (EU)", "remote": True, "salary_text": "40k EUR"}])
    text = "Acme is hiring an AI Engineer. We build agents. " * 10
    job = fetch_job("https://acme.com/careers/ai", [], client({}), llm, "haiku", lambda u: ("AI Engineer at Acme", text))
    assert (job.company, job.title, job.location, job.remote, job.salary_text) == ("Acme", "AI Engineer", "Remote (EU)", True, "40k EUR")
    assert job.description.startswith("Acme is hiring") and llm.calls[0][2] == "haiku" and "We build agents." in llm.calls[0][0]


def test_page_problems_are_reported():
    assert fetch_job("javascript:x", [], client({}), FakeLLMClient([]), "haiku", no_page) == NeedsYou(reason="Not a web page: javascript:x")
    out = fetch_job("https://acme.com/j", [], client({}), FakeLLMClient([]), "haiku", lambda u: NeedsYou(reason="Page failed to load: 404"))
    assert out == NeedsYou(reason="Page failed to load: 404")
    out = fetch_job("https://acme.com/j", [], client({}), FakeLLMClient([]), "haiku", lambda u: ("Sign in", "Sign in"))
    assert out.reason.startswith("The page has almost no text")


def test_claude_failure_falls_back_to_page_title():
    out = fetch_job("https://acme.com/j", [], client({}), FakeLLMClient([LLMError("x")]), "haiku",
                    lambda u: ("AI Engineer - Acme", "Long enough description of the job. " * 10))
    assert out.title == "AI Engineer - Acme" and out.company == "acme.com"
