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
from mcp.types import Tool as McpBaseTool
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools import MCPTool
from trpc_agent_sdk.tools.safety import BashToolBlockResponseAdapter
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


class RaisingAuditSink:

    def emit(self, event: SafetyAuditEvent) -> None:
        del event
        raise OSError("audit unavailable")


class CountingTool(BaseTool):

    def __init__(self, result: Any = None):
        super().__init__(name="CountingTool", description="Counts real handler calls")
        self.calls = 0
        self.result = result if result is not None else {"status": "executed"}
        self.last_args: dict[str, Any] | None = None

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        del tool_context
        self.calls += 1
        self.last_args = args
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
async def test_yaml_output_limit_changes_real_filter_output(tool_context, tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """
api_version: trpc-agent.io/tool-safety/v1
kind: ToolSafetyPolicy
version: "test"
policy_id: output-limit-test
limits:
  max_output_size_bytes: 4
""",
        encoding="utf-8",
    )
    tool = CountingTool({"output": "你好"})
    tool.add_one_filter(
        ToolSafetyFilter(
            SafetyScanner.from_yaml(policy_path),
            language="bash",
            content_field="command",
            output_limiter=FieldOutputLimiter("output"),
        ))

    result = await tool.run_async(tool_context=tool_context, args={"command": "echo ok"})

    assert result == {"output": "你", "output_truncated": True}


@pytest.mark.asyncio
async def test_default_timeout_is_scanned_and_passed_to_handler(tool_context):
    tool = CountingTool()
    tool.add_one_filter(
        ToolSafetyFilter(
            SafetyScanner(),
            language="bash",
            content_field="command",
            timeout_field="timeout",
            default_timeout_seconds=10,
        ))

    await tool.run_async(tool_context=tool_context, args={"command": "echo ok"})

    assert tool.calls == 1
    assert tool.last_args == {"command": "echo ok", "timeout": 10}


@pytest.mark.asyncio
async def test_default_timeout_does_not_mutate_blocked_request(tool_context):
    tool = CountingTool()
    tool.add_one_filter(
        ToolSafetyFilter(
            SafetyScanner(),
            language="bash",
            content_field="command",
            timeout_field="timeout",
            default_timeout_seconds=10,
        ))
    args = {"command": "rm -rf /"}

    result = await tool.run_async(tool_context=tool_context, args=args)

    assert result["decision"] == "deny"
    assert args == {"command": "rm -rf /"}
    assert tool.calls == 0


def test_output_limiter_handles_bytes_and_utf8_boundaries():
    limiter = FieldOutputLimiter()

    assert limiter.limit(b"abcdef", 3) == b"abc"
    assert limiter.limit("你好", 4) == "你"
    assert limiter.limit(["abc", "def"], 4) == ["abc", "d"]
    assert limiter.limit(("abc", "def"), 3) == ("abc", )


@pytest.mark.asyncio
async def test_invalid_adapter_input_returns_structured_audited_report(tool_context):
    sink = RecordingAuditSink()
    tool = CountingTool()
    tool.add_one_filter(ToolSafetyFilter(
        SafetyScanner(),
        language="bash",
        content_field="command",
        audit_sink=sink,
    ))

    result = await tool.run_async(tool_context=tool_context, args={"command": 123})

    assert result["decision"] == "needs_human_review"
    assert result["rule_id"] == "POLICY-INPUT-001"
    assert result["recommendation"]
    assert tool.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_audit_failure_does_not_change_allow_or_deny_decision(tool_context):
    safe_tool = CountingTool()
    safe_tool.add_one_filter(
        ToolSafetyFilter(
            SafetyScanner(),
            language="bash",
            content_field="command",
            audit_sink=RaisingAuditSink(),
        ))
    denied_tool = CountingTool()
    denied_tool.add_one_filter(
        ToolSafetyFilter(
            SafetyScanner(),
            language="bash",
            content_field="command",
            audit_sink=RaisingAuditSink(),
        ))

    safe_result = await safe_tool.run_async(tool_context=tool_context, args={"command": "echo ok"})
    denied_result = await denied_tool.run_async(tool_context=tool_context, args={"command": "rm -rf /"})

    assert safe_result == {"status": "executed"}
    assert safe_tool.calls == 1
    assert denied_result["decision"] == "deny"
    assert denied_tool.calls == 0


@pytest.mark.asyncio
async def test_real_bash_tool_is_blocked_before_subprocess_creation(tool_context, tmp_path):
    tool = BashTool(cwd=str(tmp_path))
    tool.add_one_filter(
        ToolSafetyFilter(
            SafetyScanner(),
            language="bash",
            content_field="command",
            tool_name=tool.name,
            block_response_adapter=BashToolBlockResponseAdapter(),
        ))

    result = await tool.run_async(tool_context=tool_context, args={"command": "rm -rf /"})

    assert result["success"] is False
    assert result["return_code"] == 126
    assert result["safety_report"]["decision"] == "deny"
    assert result["safety_report"]["rule_id"] == "FILE-001"
    assert not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_real_mcp_tool_filter_blocks_before_session_creation(tool_context):
    session_manager = MagicMock()
    safety_filter = ToolSafetyFilter(
        SafetyScanner(),
        language="bash",
        content_field="command",
        tool_name="remote_shell",
    )
    tool = MCPTool(
        mcp_tool=McpBaseTool(
            name="remote_shell",
            description="Run a remote shell command",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string"
                    }
                },
            },
        ),
        mcp_session_manager=session_manager,
        filters=[safety_filter],
    )

    result = await tool.run_async(tool_context=tool_context, args={"command": "rm -rf /"})

    assert result["decision"] == "deny"
    assert result["rule_id"] == "FILE-001"
    session_manager.create_session.assert_not_called()


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

    result = await tool.run_async(tool_context=tool_context, args={"command": "echo hello"})

    assert result["decision"] == "needs_human_review"
    assert result["analysis_status"] == "internal_error"
    assert "scanner failed" not in str(result)
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
