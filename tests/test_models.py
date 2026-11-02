import pytest
from pydantic import ValidationError

from jobcopilot.models import Evaluation, Job


def test_job_id_is_stable_hash_of_url():
    a = Job(url="https://x.com/1", source="s", company="C", title="T")
    b = Job(url="https://x.com/1", source="o", company="D", title="U")
    assert a.id == b.id
    assert len(a.id) == 12


def test_evaluation_score_bounds():
    with pytest.raises(ValidationError):
        Evaluation(job_id="a", score=101, fit_reasons="", gaps="", model="m", prompt_version="v")
