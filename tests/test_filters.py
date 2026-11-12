from jobcopilot.config import HardFilters
from jobcopilot.filters import apply_filters, rejection_reason
from jobcopilot.models import Job

F = HardFilters(
    remote_regions=["Spain", "EU", "Europe", "Worldwide"],
    onsite_cities=["Málaga"],
    exclude_title_keywords=["senior", "lead", "head of"],
    exclude_text_patterns=["us only", "5+ years"],
    min_salary_eur=30000,
)


def job(**kw) -> Job:
    base = dict(url="u", source="s", company="C", title="AI Engineer", location="Europe", remote=True)
    return Job(**(base | kw))


def test_rejects_senior_title():
    assert rejection_reason(job(title="Senior AI Engineer"), F) == "title:senior"


def test_does_not_reject_leading_substring():
    assert rejection_reason(job(title="Leadgen Engineer"), F) is None


def test_rejects_multiword_title_keyword():
    assert rejection_reason(job(title="Head of AI"), F) == "title:head of"


def test_rejects_us_only_text():
    assert rejection_reason(job(description="This role is US only."), F) == "text:us only"


def test_accepts_remote_europe():
    assert rejection_reason(job(location="Remote - Europe"), F) is None


def test_accepts_remote_empty_location():
    assert rejection_reason(job(location=""), F) is None


def test_rejects_remote_usa():
    assert rejection_reason(job(location="USA"), F) == "location"


def test_accepts_remote_france_when_listed():
    f = F.model_copy(update={"remote_regions": [*F.remote_regions, "France"]})
    assert rejection_reason(job(location="Remote - France"), f) is None
    assert rejection_reason(job(location="Remote - France"), F) == "location"


def test_rejects_onsite_france_even_when_remote_france_listed():
    f = F.model_copy(update={"remote_regions": [*F.remote_regions, "France"]})
    assert rejection_reason(job(remote=False, location="Paris, France"), f) == "location"


def test_accepts_malaga_without_accent():
    assert rejection_reason(job(remote=False, location="Malaga, Spain"), F) is None


def test_rejects_onsite_elsewhere():
    assert rejection_reason(job(remote=False, location="Berlin"), F) == "location"


def test_rejects_low_salary():
    assert rejection_reason(job(salary_text="€20k - €25k"), F) == "salary"


def test_accepts_salary_above_min():
    assert rejection_reason(job(salary_text="$40,000 - $60,000"), F) is None


def test_accepts_unparseable_salary():
    assert rejection_reason(job(salary_text="competitive"), F) is None


def test_apply_filters_keeps_only_passing():
    jobs = [job(url="a"), job(url="b", title="Senior Dev")]
    assert [j.url for j in apply_filters(jobs, F)] == ["a"]


import pytest as _pytest


@_pytest.mark.parametrize("loc", ["Remote", "REMOTE", "Anywhere", "Remote - global"])
def test_accepts_plain_remote_location(loc):
    assert rejection_reason(job(location=loc), F) is None


@_pytest.mark.parametrize("salary", ["€50.000 - €60.000", "€ 45.000,00", "$50/hour", "Up to 4.5k/month", "€3.000 brutto/Monat"])
def test_salary_parser_does_not_reject_european_or_non_annual_figures(salary):
    assert rejection_reason(job(salary_text=salary), F) is None


def test_salary_european_thousands_below_min_still_rejected():
    assert rejection_reason(job(salary_text="€20.000 - €25.000"), F) == "salary"
