import pytest
from pydantic import ValidationError

from jobcopilot.apply.models import Answer, FormField, FormSnapshot


def test_form_snapshot_roundtrip():
    snap = FormSnapshot(url="https://x", fields=[FormField(key="q1", selector="#q1", label="Why us?", kind="textarea", max_length=500)])
    assert FormSnapshot.model_validate_json(snap.model_dump_json()) == snap
    assert snap.fields[0].step == 0 and snap.fields[0].frame == ""
    with pytest.raises(ValidationError):
        FormField(key="k", selector="s", label="l", kind="slider")


def test_answer_kinds():
    assert Answer(field_key="k", label="l", kind="cv").problems == []
    with pytest.raises(ValidationError):
        Answer(field_key="k", label="l", kind="maybe")
