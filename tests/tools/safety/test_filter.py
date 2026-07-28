# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Filter integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools.safety import FieldOutputLimiter
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ToolSafetyFilter


class RecordingAuditSink:

    def __init__(self):
        self.events: list[SafetyAuditEvent] = []

    def emit(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)


class CountingTool(BaseTool):

    def __init__(self, result: Any = None):
        super().__init__(name="CountingTool", description="Counts real handler calls")
        self.calls = 0
        self.result = result if result is not None else {"status": "executed"}

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        del tool_context, args
        self.calls += 1
        return self.result


class FailingTool(CountingTool):

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        del tool_context, args
        self.calls += 1
        raise RuntimeError("handler failed")


@pytest.fixture
def tool_context():
    context = MagicMock(spec=InvocationContext)
    context.agent_context = MagicMock()
    context.agent = MagicMock()
    context.agent.before_tool_callback = None
    context.agent.after_tool_callback = None
    return context


@pytest.mark.asyncio
async def test_allow_calls_handler_and_emits_one_event(tool_context):
    sink = RecordingAuditSink()
    tool = CountingTool()
    tool.add_one_filter(
        ToolSafetyFilter(
            SafetyScanner(),
            language=ScriptLanguage.BASH,
            content_field="command",
            audit_sink=sink,
        ))

    result = await tool.run_async(tool_context=tool_context, args={"command": "echo hello"})

    assert result == {"status": "executed"}
    assert tool.calls == 1
    assert len(sink.events) == 1
    assert sink.events[0].tool_name == "CountingTool"
    assert sink.events[0].execution_blocked is False


@pytest.mark.asyncio
async def test_deny_returns_report_without_calling_handler(tool_context):
    sink = RecordingAuditSink()
    tool = CountingTool()
    tool.add_one_filter(ToolSafetyFilter(
        SafetyScanner(),
        language="bash",
        content_field="command",
        audit_sink=sink,
    ))

    result = await tool.run_async(tool_context=tool_context, args={"command": "rm -rf /"})

    assert result["decision"] == "deny"
    assert result["findings"][0]["rule_id"] == "FILE-001"
    assert tool.calls == 0
    assert len(sink.events) == 1
    assert sink.events[0].execution_blocked is True


@pytest.mark.asyncio
async def test_review_returns_report_without_calling_handler(tool_context):
    sink = RecordingAuditSink()
    tool = CountingTool()
    tool.add_one_filter(ToolSafetyFilter(
        SafetyScanner(),
        language="bash",
        content_field="command",
        audit_sink=sink,
    ))

    result = await tool.run_async(tool_context=tool_context, args={"command": "echo ok &"})

    assert result["decision"] == "needs_human_review"
    assert tool.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_explicit_output_limiter_preserves_dict_shape(tool_context):
    sink = RecordingAuditSink()
    tool = CountingTool({"output": "abcdef"})
    scanner = SafetyScanner()
    scanner.policy.limits.max_output_size_bytes = 3
    tool.add_one_filter(
        ToolSafetyFilter(
            scanner,
            language="bash",
            content_field="command",
            audit_sink=sink,
            output_limiter=FieldOutputLimiter("output"),
        ))

    result = await tool.run_async(tool_context=tool_context, args={"command": "echo hello"})

    assert result == {"output": "abc", "output_truncated": True}


@pytest.mark.asyncio
async def test_scanner_failure_fails_closed(tool_context):
    scanner = MagicMock(spec=SafetyScanner)
    scanner.scan.side_effect = RuntimeError("scanner failed")
    tool = CountingTool()
    tool.add_one_filter(ToolSafetyFilter(
        scanner,
        language="bash",
        content_field="command",
    ))

    with pytest.raises(RuntimeError, match="scanner failed"):
        await tool.run_async(tool_context=tool_context, args={"command": "echo hello"})

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_handler_failure_keeps_pre_execution_audit(tool_context):
    sink = RecordingAuditSink()
    tool = FailingTool()
    tool.add_one_filter(ToolSafetyFilter(
        SafetyScanner(),
        language="bash",
        content_field="command",
        audit_sink=sink,
    ))

    with pytest.raises(RuntimeError, match="handler failed"):
        await tool.run_async(tool_context=tool_context, args={"command": "echo hello"})

    assert tool.calls == 1
    assert len(sink.events) == 1
