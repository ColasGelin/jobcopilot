"""Small text helpers for cleaning feed content."""

import html
import re
import unicodedata

_TAG = re.compile(r"<[^>]*(?:>|$)")
_WS = re.compile(r"\s+")


def html_to_text(s: str) -> str:
    """Unescape entities, strip tags and collapse whitespace."""
    s = html.unescape(s or "")
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()


def normalize(s: str) -> str:
    """Lowercase, strip accents and collapse whitespace, for comparisons."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _WS.sub(" ", s.lower()).strip()
