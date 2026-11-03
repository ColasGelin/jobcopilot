import pytest

from jobcopilot.apply.ai_policy import find_ai_ban


@pytest.mark.parametrize("text", [
    "Please do not use AI to write your answers.",
    "Answers must be written without the use of AI tools.",
    "AI-generated applications will be rejected.",
    "We ask candidates not to use ChatGPT or generative AI.",
    "Please don't use AI tools when answering these questions.",
    "Applications written by AI will not be considered.",
])
def test_ai_ban_found(text):
    assert find_ai_ban("Intro. " + text + " Thanks.") == text


@pytest.mark.parametrize("text", [
    "We build AI tools for developers.",
    "Experience with generative AI is a plus.",
    "You will use AI every day to ship faster.",
    "",
])
def test_ai_ban_not_found(text):
    assert find_ai_ban(text) is None


def test_ai_ban_quote_is_trimmed():
    long = "Do not use AI " + "really " * 100 + "ever."
    assert len(find_ai_ban(long)) <= 300


def test_ai_policy_field_counts_as_a_ban():
    from jobcopilot.apply.ai_policy import find_ai_policy_field
    from jobcopilot.apply.models import FormField

    policy = FormField(key="q", selector="#q", label="AI Policy for Application", kind="select", options=["Yes", "No"],
                       description="We invite you to review our AI partnership guidelines for candidates and confirm by selecting Yes.")
    other = FormField(key="w", selector="#w", label="Why us?", kind="textarea")
    quote = find_ai_policy_field([other, policy])
    assert quote.startswith("AI Policy for Application: We invite you to review") and find_ai_policy_field([other]) is None
