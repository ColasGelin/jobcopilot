import pytest

from jobcopilot.apply.style import ai_tells


@pytest.mark.parametrize("text", [
    "The hard part is not building an LLM demo, it's keeping it trustworthy once real people depend on it.",
    "This isn't just a job for me, it's the work I want to do.",
    "Not only do I build agents, but I also test them.",
    "Lo difícil no es construir una demo, es mantenerla fiable.",
    "No solo construyo agentes sino que también los evalúo.",
    "Le plus dur n'est pas de faire une démo, c'est de la rendre fiable.",
    "A wrong answer is a compliance problem, not a small bug.",
    "Es un problema de cumplimiento, no un simple error.",
    "C'est un problème de conformité, pas un simple bug.",
])
def test_contrast_pattern_is_flagged_in_each_language(text):
    problems = ai_tells("cover letter", text)
    assert len(problems) == 1 and "not X, it's Y" in problems[0]


def test_qualifyze_letter_opening_quotes_the_phrase():
    text = "What I like here is that the hard part is not building an LLM demo, it's keeping it trustworthy."
    assert '("not building an LLM demo, it\'s")' in ai_tells("cover letter", text)[0]


@pytest.mark.parametrize("text", ["I built aibench — it runs every night.", "I built aibench – it runs every night."])
def test_long_dashes_are_flagged(text):
    assert ai_tells("Why us?", text) == ["Why us? uses a long dash (—): use a comma, a colon or two sentences"]


def test_stock_phrases_listed_once_each():
    text = "I'm excited to apply. What caught my attention is your dynamic team. I am excited, again. Me apasiona la IA."
    assert ai_tells("cover letter", text) == [
        "cover letter uses stock phrases (caught my attention, dynamic team, i am excited, i'm excited, me apasiona): use plain words of your own"
    ]


@pytest.mark.parametrize("text", [
    "I built an MCP server so several agents share the same tools. I am not senior yet, but I ship.",
    "I can't do on-site work outside Málaga. My CV is attached.",
    "Trabajo con LangGraph y Qdrant en Málaga; no tengo experiencia con GCP.",
    "Je ne parle pas allemand, mais je parle espagnol.",
    "Python, FastAPI, Docker - and Kubernetes.",
])
def test_plain_writing_passes(text):
    assert ai_tells("answer", text) == []
