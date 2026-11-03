from pathlib import Path

import pytest

from conftest import make_job
from jobcopilot.apply.draft import draft_answers
from jobcopilot.apply.models import FormField, FormSnapshot
from jobcopilot.apply.voice import Voice, load_voice
from jobcopilot.config import ApplicationProfile
from jobcopilot.cv.bank import load_bank
from jobcopilot.llm import FakeLLMClient, LLMError, LLMUnavailable

EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"
PROFILE = ApplicationProfile(first_name="Alex", email="alex@example.com", salary_expectation="45,000 EUR")
VOICE = Voice(samples=["Ive built many things. The most interesting to me is the MCP server."], notes="No buzzwords.",
              recent=[("Why us?", "Because I like agents.")])


def f(key, label, kind="text", **kw):
    return FormField(key=key, selector=f"#{key}", label=label, kind=kind, **kw)


FIELDS = [
    f("first_name", "First name"), f("email", "Email", "email"),
    f("why", "Why do you want to join?", "textarea", max_length=500),
    f("visa", "Do you need visa sponsorship?", "select", options=["Yes", "No"]),
    f("cv", "Resume/CV", "file"), f("cover", "Cover letter", "file"),
    f("colour", "Favourite colour"),
]
GOOD = {"answers": [
    {"key": "first_name", "kind": "fact", "profile_key": "first_name"},
    {"key": "email", "kind": "fact", "profile_key": "email"},
    {"key": "why", "kind": "open", "answer": "I built a RAG assistant with LangChain and Qdrant."},
    {"key": "visa", "kind": "open", "option": "No"},
    {"key": "cv", "kind": "leave"}, {"key": "cover", "kind": "open", "answer": "x"},
    {"key": "colour", "kind": "leave"},
]}


@pytest.fixture
def bank():
    return load_bank(EXAMPLE)


def run(bank, fields, responses, ai_ban=False, profile=PROFILE):
    llm = FakeLLMClient(list(responses))
    job = make_job(1, company="Acme", title="LLM Engineer", description="We build agents.")
    form = FormSnapshot(url="https://x", fields=fields)
    return draft_answers(job, form, fields, profile, bank, VOICE, llm, "sonnet", ai_ban), llm


def by_key(answers):
    return {a.field_key: a for a in answers}


def test_facts_open_cv_and_leave(bank):
    answers, llm = run(bank, FIELDS, [GOOD])
    a = by_key(answers)
    assert [x.field_key for x in answers] == [x.key for x in FIELDS]
    assert (a["first_name"].kind, a["first_name"].draft, a["first_name"].profile_key) == ("fact", "Alex", "first_name")
    assert a["email"].draft == "alex@example.com"
    assert a["why"].kind == "open" and "RAG" in a["why"].draft and a["why"].problems == []
    assert (a["visa"].kind, a["visa"].draft) == ("open", "No")
    assert (a["cv"].kind, a["cv"].draft) == ("cv", "CV")
    assert a["cover"].kind == "leave" and a["colour"].kind == "leave"
    assert len(llm.calls) == 1 and llm.calls[0][2] == "sonnet"


def test_eeo_and_consent_forced_leave_any_language(bank):
    labels = ["Gender", "Género", "Sexe", "Discapacidad", "Handicap", "Origine ethnique", "Veteran status",
              "I agree to the privacy policy", "Agreement to Arbitrate", "Acepto los términos"]
    fields = [f(f"k{i}", label, "select", options=["Yes", "No"]) for i, label in enumerate(labels)]
    resp = {"answers": [{"key": x.key, "kind": "open", "option": "Yes"} for x in fields]}
    answers, _ = run(bank, fields, [resp])
    assert all(a.kind == "leave" and a.draft == "" for a in answers)


def test_unknown_or_empty_profile_key_is_left(bank):
    fields = [f("phone", "Phone", "tel"), f("x", "X")]
    resp = {"answers": [{"key": "phone", "kind": "fact", "profile_key": "phone"}, {"key": "x", "kind": "fact", "profile_key": "shoe_size"}]}
    answers, _ = run(bank, fields, [resp])
    assert [a.kind for a in answers] == ["leave", "leave"]


def test_fact_option_must_be_listed(bank):
    fields = [f("visa", "Visa sponsorship", "select", options=["Yes", "No"])]
    bad = {"answers": [{"key": "visa", "kind": "fact", "profile_key": "email", "option": "Maybe"}]}
    answers, llm = run(bank, fields, [bad, bad])
    assert len(llm.calls) == 2 and "Maybe" in answers[0].problems[0]


def test_invented_fact_retried_then_kept_with_problems(bank):
    bad = {"answers": [{"key": "why", "kind": "open", "answer": "I deployed agents on AWS."}]}
    answers, llm = run(bank, [FIELDS[2]], [bad, bad])
    assert len(llm.calls) == 2 and "AWS" in llm.calls[1][0]
    assert answers[0].draft == "I deployed agents on AWS." and "AWS" in answers[0].problems[0]


def test_retry_fixes(bank):
    bad = {"answers": [{"key": "why", "kind": "open", "answer": "I deployed agents on AWS."}]}
    good = {"answers": [{"key": "why", "kind": "open", "answer": "I built a RAG assistant."}]}
    answers, llm = run(bank, [FIELDS[2]], [bad, good])
    assert len(llm.calls) == 2 and answers[0].problems == []


def test_salary_from_profile_allowed_in_open_answer(bank):
    resp = {"answers": [{"key": "why", "kind": "open", "answer": "I expect 45,000 EUR."}]}
    answers, _ = run(bank, [FIELDS[2]], [resp])
    assert answers[0].problems == []


def test_length_limits(bank):
    long = {"answers": [{"key": "why", "kind": "open", "answer": "word " * 120}]}  # 600 chars > 500
    answers, _ = run(bank, [FIELDS[2]], [long, long])
    assert "500" in answers[0].problems[0]
    free = f("more", "Anything else?", "textarea")
    answers, _ = run(bank, [free], [{"answers": [{"key": "more", "kind": "open", "answer": "word " * 151}]}] * 2)
    assert "150" in answers[0].problems[0]
    guided = f("why2", "Why Acme?", "textarea", description="Great answers are 200-400 words.")
    answers, _ = run(bank, [guided], [{"answers": [{"key": "why2", "kind": "open", "answer": "word " * 300}]}])
    assert answers[0].problems == []


def test_ai_ban_leaves_open_empty_and_prompt_says_so(bank):
    answers, llm = run(bank, FIELDS, [GOOD], ai_ban=True)
    a = by_key(answers)
    assert a["why"].kind == "open" and a["why"].draft == ""
    assert a["visa"].draft == "No" and a["email"].draft == "alex@example.com"  # choices and facts still filled
    assert "do not write" in llm.calls[0][0].lower()


def test_prompt_contains_voice_limits_and_no_profile_values(bank):
    _, llm = run(bank, FIELDS, [GOOD])
    prompt = llm.calls[0][0]
    for text in ("The most interesting to me is the MCP server.", "Because I like agents.", "No buzzwords.",
                 "500 characters", "LLM Engineer", "We build agents.", "salary_expectation", "acme.rag"):
        assert text in prompt
    assert "alex@example.com" not in prompt and "{" + "job_title}" not in prompt


def test_bad_shape_twice_raises_llm_error(bank):
    with pytest.raises(LLMError):
        run(bank, FIELDS, [{"nope": 1}, {"nope": 2}])


def test_unavailable_propagates(bank):
    with pytest.raises(LLMUnavailable):
        run(bank, FIELDS, [LLMUnavailable("logged out")])


def test_step_copied_from_field(bank):
    field = f("why", "Why?", "textarea", step=2)
    answers, _ = run(bank, [field], [{"answers": [{"key": "why", "kind": "open", "answer": "I built a RAG assistant."}]}])
    assert answers[0].step == 2


def test_load_voice(tmp_path):
    assert load_voice(tmp_path / "missing", []) == Voice(samples=[], notes="", recent=[])
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "02.md").write_text("second")
    (tmp_path / "samples" / "01.md").write_text("first")
    (tmp_path / "notes.md").write_text("short sentences\n")
    v = load_voice(tmp_path, [("q", "a")])
    assert v.samples == ["first", "second"] and v.notes == "short sentences" and v.recent == [("q", "a")]


def test_policy_acknowledgement_left(bank):
    fields = [f("p", "AI Policy for Application", "select", options=["Yes", "No"])]
    answers, _ = run(bank, fields, [{"answers": [{"key": "p", "kind": "open", "option": "Yes"}]}])
    assert answers[0].kind == "leave"
