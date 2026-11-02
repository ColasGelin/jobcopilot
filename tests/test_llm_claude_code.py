import json
import subprocess

import pytest

from jobcopilot.llm import ClaudeCodeClient, FakeLLMClient, LLMError, LLMUnavailable

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}
LOGGED_OUT = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "result": "Failed to authenticate: OAuth session expired and could not be refreshed",
}


def runner_returning(payload, returncode=0):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(argv, returncode, stdout=out, stderr="")

    run.calls = calls
    return run


def test_returns_structured_output():
    client = ClaudeCodeClient(runner=runner_returning({"is_error": False, "result": "", "structured_output": {"x": 1}}))
    assert client.complete_json("p", SCHEMA, "haiku") == {"x": 1}


def test_falls_back_to_fenced_json_in_result():
    client = ClaudeCodeClient(runner=runner_returning({"is_error": False, "result": "```json\n{\"x\": 2}\n```"}))
    assert client.complete_json("p", SCHEMA, "haiku") == {"x": 2}


def test_logged_out_raises_unavailable():
    client = ClaudeCodeClient(runner=runner_returning(LOGGED_OUT, returncode=1))
    with pytest.raises(LLMUnavailable, match="logged out"):
        client.complete_json("p", SCHEMA, "haiku")


def test_other_error_raises_llm_error_not_unavailable():
    client = ClaudeCodeClient(runner=runner_returning({"is_error": True, "result": "Overloaded"}))
    with pytest.raises(LLMError) as exc:
        client.complete_json("p", SCHEMA, "haiku")
    assert not isinstance(exc.value, LLMUnavailable)


def test_missing_binary_raises_unavailable():
    def run(argv, **kw):
        raise FileNotFoundError(argv[0])
    with pytest.raises(LLMUnavailable, match="not found"):
        ClaudeCodeClient(runner=run).complete_json("p", SCHEMA, "haiku")


def test_timeout_raises_llm_error():
    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)
    with pytest.raises(LLMError):
        ClaudeCodeClient(runner=run).complete_json("p", SCHEMA, "haiku")


def test_non_json_stdout_raises_llm_error():
    with pytest.raises(LLMError):
        ClaudeCodeClient(runner=runner_returning("not json")).complete_json("p", SCHEMA, "haiku")


def test_command_disables_tools_and_passes_prompt_on_stdin():
    run = runner_returning({"is_error": False, "structured_output": {"x": 1}})
    ClaudeCodeClient(runner=run).complete_json("hello", SCHEMA, "sonnet")
    argv, kw = run.calls[0]
    assert argv[argv.index("--tools") + 1] == ""
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert kw["input"] == "hello"


def test_fake_client_returns_and_raises_in_order():
    fake = FakeLLMClient([{"a": 1}, LLMError("x")])
    assert fake.complete_json("p", {}, "m") == {"a": 1}
    with pytest.raises(LLMError):
        fake.complete_json("q", {}, "m")
    assert [c[0] for c in fake.calls] == ["p", "q"]
