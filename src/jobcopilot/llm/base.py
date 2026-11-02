"""The interface every LLM backend implements, plus a fake for tests."""

from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """A single LLM call failed; callers may retry."""


class LLMUnavailable(LLMError):
    """The backend cannot be used at all (missing binary, logged out); callers abort the run."""


class LLMClient(Protocol):
    def complete_json(self, prompt: str, schema: dict, model: str) -> dict: ...


class FakeLLMClient:
    """Returns (or raises) the given responses in order and records every call."""

    def __init__(self, responses: list[dict | Exception]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, str]] = []

    def complete_json(self, prompt: str, schema: dict, model: str) -> dict:
        self.calls.append((prompt, schema, model))
        if not self.responses:
            raise AssertionError("FakeLLMClient ran out of responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
