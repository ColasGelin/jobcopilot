import httpx

from conftest import make_job
from jobcopilot.apply.resolve import EASY_APPLY, explain, find_on_ats, same_role, slug_candidates


def test_slug_candidates_drop_legal_suffixes_and_emoji():
    assert slug_candidates("Product Madness 📱🎮") == ["productmadness", "product-madness"]
    assert slug_candidates("Secture S.L.") == ["secture"]
    assert slug_candidates("Acme, Inc.") == ["acme"]
    assert slug_candidates("") == []


def test_same_role_ignores_places_and_punctuation():
    assert same_role("AI Software engineer (Madrid, España)", "AI Software Engineer")
    assert same_role("Junior AI & Generative AI Engineer", "Junior AI and Generative AI Engineer")
    assert not same_role("AI Engineer", "Senior AI Engineer, Platform")


def board(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_finds_the_posting_on_the_company_board():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "api.ashbyhq.com" and request.url.path.endswith("/menhir-ai"):
            return httpx.Response(200, json={"jobs": [
                {"title": "Backend Engineer", "jobUrl": "https://jobs.ashbyhq.com/menhir-ai/1"},
                {"title": "Junior AI Engineer", "jobUrl": "https://jobs.ashbyhq.com/menhir-ai/2"}]})
        return httpx.Response(404)

    job = make_job(1, company="Menhir AI", title="Junior AI Engineer")
    assert find_on_ats(job, board(handler)) == "https://jobs.ashbyhq.com/menhir-ai/2"
    assert "boards-api.greenhouse.io" in seen


def test_greenhouse_and_lever_postings_are_found_too():
    def handler(request):
        if request.url.host == "boards-api.greenhouse.io" and "/boards/acme/" in request.url.path:
            return httpx.Response(200, json={"jobs": [{"title": "AI Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/9"}]})
        if request.url.host == "api.lever.co" and request.url.path.endswith("/beta"):
            return httpx.Response(200, json=[{"text": "ML Engineer", "hostedUrl": "https://jobs.lever.co/beta/x"}])
        return httpx.Response(404)

    assert find_on_ats(make_job(1, company="Acme", title="AI Engineer"), board(handler)) == "https://boards.greenhouse.io/acme/jobs/9"
    assert find_on_ats(make_job(2, company="Beta", title="ML Engineer"), board(handler)) == "https://jobs.lever.co/beta/x"


def test_nothing_found_when_no_board_has_the_role_or_the_network_fails():
    def handler(request):
        if request.url.host == "api.lever.co":
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"jobs": [{"title": "Sales Lead", "absolute_url": "https://x/1", "jobUrl": "https://x/1"}]})

    assert find_on_ats(make_job(1, company="Acme", title="AI Engineer"), board(handler)) is None


def test_explain_linkedin_without_a_form():
    assert explain("https://www.linkedin.com/jobs/view/1", "No application form found") == EASY_APPLY
    assert explain("https://www.linkedin.com/jobs/view/1", "Sign-in or account creation required") == EASY_APPLY
    assert explain("https://acme.com/jobs/1", "No application form found") == "No application form found"


def test_recruitee_workable_and_bamboohr_boards():
    def handler(request):
        host = request.url.host
        if host == "gamma.recruitee.com":
            return httpx.Response(200, json={"offers": [{"title": "AI Platform Engineer", "careers_url": "https://gamma.recruitee.com/o/ai"}]})
        if host == "apply.workable.com" and request.url.path.endswith("/delta"):
            return httpx.Response(200, json={"jobs": [{"title": "ML Engineer", "url": "https://apply.workable.com/j/D1"}]})
        if host == "omega.bamboohr.com":
            return httpx.Response(200, json={"result": [{"id": "71", "jobOpeningName": "AI Engineer (Offensive Security)"}]})
        return httpx.Response(404)

    c = board(handler)
    assert find_on_ats(make_job(1, company="Gamma", title="AI Platform Engineer"), c) == "https://gamma.recruitee.com/o/ai"
    assert find_on_ats(make_job(2, company="Delta", title="ML Engineer"), c) == "https://apply.workable.com/j/D1"
    assert find_on_ats(make_job(3, company="Omega", title="AI Engineer (Offensive Security)"), c) == "https://omega.bamboohr.com/careers/71"
