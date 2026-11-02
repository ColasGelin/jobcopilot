import pytest

from jobcopilot.config import HardFilters, Profile
from jobcopilot.models import Job


@pytest.fixture
def profile() -> Profile:
    return Profile(hard_filters=HardFilters(), summary="Junior AI engineer, Python, RAG, agents.")


def make_job(i: int, **kw) -> Job:
    base = dict(url=f"https://jobs/{i}", source="test", company=f"Co{i}", title=f"AI Engineer {i}", remote=True, location="Europe")
    return Job(**(base | kw))
