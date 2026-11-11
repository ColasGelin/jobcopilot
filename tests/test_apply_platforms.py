import pytest

from jobcopilot.apply.platforms import form_url


@pytest.mark.parametrize("url,expected", [
    ("https://jobs.ashbyhq.com/alan/d4762f23-0a9d-42d5-b059-7078d60f2837",
     "https://jobs.ashbyhq.com/alan/d4762f23-0a9d-42d5-b059-7078d60f2837/application"),
    ("https://jobs.lever.co/tinybird/22cbcc27-89c4-4f87-87bc-6937712e7b10?lever-source=Indeed",
     "https://jobs.lever.co/tinybird/22cbcc27-89c4-4f87-87bc-6937712e7b10/apply"),
    ("https://apply.workable.com/realadvisor/j/A70CD519A5/", "https://apply.workable.com/realadvisor/j/A70CD519A5/apply/"),
    ("https://qualifyzegmbh.recruitee.com/o/ai-platform-engineer-1?source=Indeed",
     "https://qualifyzegmbh.recruitee.com/o/ai-platform-engineer-1/c/new"),
    ("https://acme.teamtailor.com/jobs/123-python-dev", "https://acme.teamtailor.com/jobs/123-python-dev/applications/new"),
    ("https://acme.teamtailor.com/es/jobs/123-python-dev?x=1", "https://acme.teamtailor.com/es/jobs/123-python-dev/applications/new"),
])
def test_known_platforms(url, expected):
    assert form_url(url) == expected


def test_teamtailor_on_the_company_domain_is_recognised_by_its_html():
    url = "https://job.tecdata.es/jobs/8668139-desarrollador-a-python-ia"
    assert form_url(url) is None
    assert form_url(url, '<link href="https://assets.teamtailor-cdn.com/x.css">') == url + "/applications/new"


@pytest.mark.parametrize("url", [
    "https://apply.workable.com/j/A70CD519A5",  # short link: read once it has redirected
    "https://jobs.ashbyhq.com/alan",  # the company's board, not a job
    "https://jobs.ashbyhq.com/alan/d4762f23-0a9d-42d5-b059-7078d60f2837/application",  # already the form
    "https://www.linkedin.com/jobs/view/1",
    "https://example.com/jobs/12-dev",  # /jobs/<id> alone is not Teamtailor
])
def test_no_guess_for_other_pages(url):
    assert form_url(url) is None
