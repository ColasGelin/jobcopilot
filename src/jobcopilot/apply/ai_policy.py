"""Spot postings that ask candidates not to use AI for their answers."""

from __future__ import annotations

import re

MAX_QUOTE = 300

_AI = r"(?:AI|A\.I\.|ChatGPT|LLMs?|generative AI|AI tools?|artificial intelligence)"
_PATTERNS = [
    rf"\b(?:do not|don't|please don't|not to|must not|should not|shouldn't)\s+use\s+(?:any\s+)?(?:of\s+)?(?:\w+\s+)?{_AI}",
    rf"\bwithout\s+(?:the\s+use\s+of|using|the\s+help\s+of|help\s+from)\s+(?:any\s+)?{_AI}",
    rf"\b{_AI}[- ]generated\s+(?:answers?|responses?|applications?|content|cover letters?)",
    rf"\b(?:written|generated|produced)\s+(?:by|with|using)\s+{_AI}\s+(?:will|are|is|may)",
    rf"\bno\s+{_AI}[- ](?:generated|written|assisted)",
]
_REGEX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I)
_SENTENCE_END = re.compile(r"[.!?\n]")


def find_ai_ban(page_text: str) -> str | None:
    """The sentence asking not to use AI, or None."""
    m = _REGEX.search(page_text)
    if m is None:
        return None
    start = max((e.end() for e in _SENTENCE_END.finditer(page_text, 0, m.start())), default=0)
    end_match = _SENTENCE_END.search(page_text, m.end())
    end = end_match.end() if end_match else len(page_text)
    return page_text[start:end].strip()[:MAX_QUOTE]
