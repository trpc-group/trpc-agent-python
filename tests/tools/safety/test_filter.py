# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""ToolSafetyFilter tests."""

from types import SimpleNamespace

import pytest

from trpc_agent_sdk.filter import run_filters
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterRunner
from trpc_agent_sdk.tools.safety import CompositeAuditSink
from trpc_agent_sdk.tools import reset_tool_var
from trpc_agent_sdk.tools import set_tool_var
from trpc_agent_sdk.tools.safety import SafetyAuditError
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy


class _MemorySink:

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class _FailingSink:

    def emit(self, event):
        del event
        raise SafetyAuditError("audit unavailable")


class _ExpandingAfterFilter(BaseFilter):

    async def _after(self, ctx, req, rsp):
        del ctx, req
        rsp.rsp = {"stdout": "x" * 100}


class _Runner(FilterRunner):
    pass


def _filter(sink, max_output=100):
    policy = ToolSafetyPolicy(
        allowed_commands=["echo"],
        max_timeout_seconds=10,
        max_output_bytes=max_output,
    )
    return ToolSafetyFilter(ToolScriptSafetyGuard(policy), sink)


def test_filter_from_policy(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("version: 1\nallowed_commands: [echo]\n", encoding="utf-8")
    safety_filter = ToolSafetyFilter.from_policy(str(path), _MemorySink())
    assert safety_filter._guard.policy.allowed_commands == ["echo"]


async def _run_filter(safety_filter, args, handler, tool_name="Bash"):
    tool = SimpleNamespace(name=tool_name, description="shell")
    token = set_tool_var(tool)
    try:
        return await run_filters(SimpleNamespace(), args, [safety_filter], handler)
    finally:
        reset_tool_var(token)


@pytest.mark.asyncio
async def test_allow_invokes_handler_and_injects_timeout():
    sink = _MemorySink()
    args = {"command": "echo ok", "timeout": 0}
    called = False

    async def handler():
        nonlocal called
        called = True
        return {"stdout": "ok"}

    result = await _run_filter(_filter(sink), args, handler)

    assert called is True
    assert result["stdout"] == "ok"
    assert args["timeout"] == 10
    assert sink.events[0].execution_blocked is False


@pytest.mark.asyncio
async def test_deny_stops_handler_and_returns_report():
    sink = _MemorySink()
    called = False

    async def handler():
        nonlocal called
        called = True

    result = await _run_filter(_filter(sink), {"command": "rm -rf /"}, handler)

    assert called is False
    assert result["decision"] == "deny"
    assert sink.events[0].execution_blocked is True


@pytest.mark.asyncio
async def test_unknown_execution_tool_does_not_fail_open():

    async def handler():
        raise AssertionError("handler must not run")

    result = await _run_filter(
        _filter(_MemorySink()),
        {"command": "rm -rf /"},
        handler,
        tool_name="custom_exec",
    )
    assert result["decision"] == "deny"


@pytest.mark.asyncio
async def test_review_stops_handler():

    async def handler():
        raise AssertionError("handler must not run")

    result = await _run_filter(_filter(_MemorySink()), {"command": "uname -a"}, handler)
    assert result["decision"] == "needs_human_review"


@pytest.mark.asyncio
async def test_audit_failure_stops_handler():

    async def handler():
        raise AssertionError("handler must not run")

    result = await _run_filter(_filter(_FailingSink()), {"command": "echo ok"}, handler)
    assert result == {
        "error": "TOOL_SAFETY_AUDIT_FAILED",
        "decision": "deny",
        "execution_blocked": True,
    }


@pytest.mark.asyncio
async def test_primary_audit_degradation_stops_handler():
    fallback = _MemorySink()
    sink = CompositeAuditSink(_FailingSink(), fallback)

    async def handler():
        raise AssertionError("handler must not run")

    result = await _run_filter(_filter(sink), {"command": "echo ok"}, handler)
    assert result["error"] == "TOOL_SAFETY_AUDIT_FAILED"
    assert fallback.events[0].execution_blocked is True


@pytest.mark.asyncio
async def test_stream_audit_failure_returns_structured_error():
    runner = _Runner(filters=[_filter(_FailingSink())])
    token = set_tool_var(SimpleNamespace(name="Bash", description="shell"))

    async def handler():
        raise AssertionError("handler must not run")
        yield

    try:
        events = [
            event async for event in runner._run_stream_filters(
                SimpleNamespace(),
                {"command": "echo ok"},
                handler,
            )
        ]
    finally:
        reset_tool_var(token)
    assert events == [{
        "error": "TOOL_SAFETY_AUDIT_FAILED",
        "decision": "deny",
        "execution_blocked": True,
    }]


@pytest.mark.asyncio
async def test_scan_error_returns_sanitized_blocking_report():
    sink = _MemorySink()

    async def handler():
        raise AssertionError("handler must not run")

    result = await _run_filter(_filter(sink), ["password='top secret phrase'"], handler)
    assert result["decision"] == "needs_human_review"
    assert "top secret phrase" not in str(result)


@pytest.mark.asyncio
async def test_after_limits_output():

    async def handler():
        return {"stdout": "x" * 20, "stderr": "y" * 20}

    result = await _run_filter(_filter(_MemorySink(), max_output=10), {"command": "echo ok"}, handler)
    assert result["truncated"] is True
    assert len((result["stdout"] + result["stderr"]).encode()) <= 10


@pytest.mark.asyncio
async def test_after_limits_list_output():

    async def handler():
        return ["x" * 20, "y" * 20]

    result = await _run_filter(_filter(_MemorySink(), max_output=10), {"command": "echo ok"}, handler)
    assert result == ["x" * 10, ""]


@pytest.mark.asyncio
async def test_non_applicable_timeout_is_not_injected():
    args = {"timeout": 3}

    async def handler():
        return {"ok": True}

    result = await _run_filter(_filter(_MemorySink()), args, handler, tool_name="unrelated")
    assert result == {"ok": True}
    assert args["timeout"] == 3


@pytest.mark.asyncio
async def test_final_limit_runs_after_outer_filter():
    runner = _Runner(filters=[_filter(_MemorySink(), max_output=10), _ExpandingAfterFilter()])
    tool = SimpleNamespace(name="Bash", description="shell")
    token = set_tool_var(tool)

    async def handler():
        return {"stdout": "ok"}

    try:
        result = await runner._run_filters(SimpleNamespace(), {"command": "echo ok"}, handler)
    finally:
        reset_tool_var(token)
    assert result["truncated"] is True
    assert len(result["stdout"].encode()) <= 10
