"""LLM backend that shells out to the Claude Code CLI in headless mode (uses the user's subscription)."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from typing import Callable

from jobcopilot.llm.base import LLMError, LLMUnavailable

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_AUTH_HINTS = ("authenticat", "log in", "login")


class ClaudeCodeClient:
    def __init__(self, timeout_s: int = 300, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run, binary: str = "claude"):
        self.timeout_s = timeout_s
        self.runner = runner
        self.binary = binary

    def complete_json(self, prompt: str, schema: dict, model: str) -> dict:
        argv = [
            self.binary, "-p",
            "--output-format", "json",
            "--model", model,
            "--json-schema", json.dumps(schema),
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        try:
            with tempfile.TemporaryDirectory(prefix="jobcopilot-") as cwd:
                proc = self.runner(argv, input=prompt, capture_output=True, text=True, timeout=self.timeout_s, cwd=cwd)
        except FileNotFoundError as e:
            raise LLMUnavailable("claude CLI not found; install Claude Code and run `claude` once to log in") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude timed out after {self.timeout_s}s") from e

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude returned non-JSON output: {proc.stdout[:200]!r} {proc.stderr[:200]!r}") from e

        result = payload.get("result") or ""
        if payload.get("is_error"):
            if any(h in result.lower() for h in _AUTH_HINTS):
                raise LLMUnavailable(
                    "Claude Code is logged out: run `claude` in a terminal and /login "
                    "(in Docker: set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token` in .env)"
                )
            raise LLMError(f"claude error: {result[:300]}")

        if isinstance(payload.get("structured_output"), dict):
            return payload["structured_output"]
        text = result.strip()
        match = _FENCE.match(text)
        if match:
            text = match.group(1)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude result is not JSON: {text[:200]!r}") from e
        if not isinstance(data, dict):
            raise LLMError("claude result is not a JSON object")
        return data
