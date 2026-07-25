# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Integration tests for tool safety filters, executors, and telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import MemoryAuditSink
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanner
from trpc_agent_sdk.tools.safety import ToolScriptSafetyFilter
from trpc_agent_sdk.types import Outcome


class RecordingExecutor(BaseCodeExecutor):
    """Minimal executor that records whether delegated execution happened."""

    calls: int = 0
    output: str = "delegated output"

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ):
        self.calls += 1
        return create_code_execution_result(stdout=self.output)


@pytest.fixture
def scanner() -> ToolSafetyScanner:
    """Scanner used by integration tests."""
    return ToolSafetyScanner(
        ToolSafetyPolicy(
            allowed_domains=["api.example.com"],
            allowed_commands=["echo"],
            denied_paths=["/etc", "~/.ssh", ".env"],
            max_timeout_seconds=10,
            max_output_bytes=64,
        ))


def _tool_context() -> MagicMock:
    context = MagicMock(spec=InvocationContext)
    context.agent_context = MagicMock()
    context.agent = MagicMock()
    context.agent.before_tool_callback = None
    context.agent.after_tool_callback = None
    return context


@pytest.mark.asyncio
async def test_tool_filter_blocks_before_function_execution(scanner: ToolSafetyScanner):
    """A denied tool call never reaches the wrapped function."""
    executed = False
    audit = MemoryAuditSink()

    def run_script(command: str):
        nonlocal executed
        executed = True
        return {"command": command}

    guard = ToolScriptSafetyFilter(scanner=scanner, audit_sink=audit)
    tool = FunctionTool(run_script, filters=[guard])
    result = await tool.run_async(tool_context=_tool_context(), args={"command": "rm -rf /tmp/data"})

    assert executed is False
    assert result["error"] == "TOOL_SAFETY_BLOCKED"
    assert result["safety_report"]["decision"] == Decision.DENY.value
    assert len(audit.events) == 1
    assert audit.events[0].blocked is True
    assert audit.events[0].tool_name == "run_script"


@pytest.mark.asyncio
async def test_tool_filter_blocks_review_until_human_approval(scanner: ToolSafetyScanner):
    """Review decisions fail closed in unattended tool execution."""
    guard = ToolScriptSafetyFilter(scanner=scanner, audit_sink=MemoryAuditSink())

    def run_script(command: str):
        return {"command": command}

    tool = FunctionTool(run_script, filters=[guard])
    result = await tool.run_async(
        tool_context=_tool_context(),
        args={"command": "printf ok | tee output.txt"},
    )

    assert result["error"] == "TOOL_SAFETY_REVIEW_REQUIRED"
    assert result["safety_report"]["decision"] == Decision.NEEDS_HUMAN_REVIEW.value


@pytest.mark.asyncio
async def test_tool_filter_does_not_drop_string_command_args(scanner: ToolSafetyScanner):
    """Non-list argument containers remain visible to context scanning."""
    executed = False

    def run_script(command: str, args: str):
        nonlocal executed
        executed = True
        return {"command": command, "args": args}

    tool = FunctionTool(
        run_script,
        filters=[ToolScriptSafetyFilter(scanner=scanner, audit_sink=MemoryAuditSink())],
    )
    result = await tool.run_async(
        tool_context=_tool_context(),
        args={
            "command": "echo safe",
            "args": "; rm -rf /"
        },
    )

    assert executed is False
    assert result["error"] == "TOOL_SAFETY_BLOCKED"
    assert "ARG-001" in result["safety_report"]["rule_ids"]


@pytest.mark.asyncio
async def test_tool_filter_allows_safe_execution(scanner: ToolSafetyScanner):
    """Allowed calls continue through the normal Tool filter chain."""
    audit = MemoryAuditSink()

    def run_script(command: str):
        return {"executed": command}

    tool = FunctionTool(
        run_script,
        filters=[ToolScriptSafetyFilter(scanner=scanner, audit_sink=audit)],
    )
    result = await tool.run_async(tool_context=_tool_context(), args={"command": "echo safe"})

    assert result == {"executed": "echo safe"}
    assert audit.events[0].decision == Decision.ALLOW
    assert audit.events[0].blocked is False


@pytest.mark.asyncio
async def test_code_executor_wrapper_blocks_and_delegates(scanner: ToolSafetyScanner):
    """The executor wrapper blocks unsafe code and delegates safe code."""
    delegate = RecordingExecutor()
    audit = MemoryAuditSink()
    guarded = SafetyGuardedCodeExecutor(executor=delegate, scanner=scanner, audit_sink=audit)

    denied = await guarded.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="open('~/.ssh/id_rsa').read()")]),
    )
    allowed = await guarded.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print(2 + 2)")]),
    )

    assert denied.outcome == Outcome.OUTCOME_FAILED
    assert "TOOL_SAFETY_BLOCKED" in denied.output
    assert allowed.outcome == Outcome.OUTCOME_OK
    assert delegate.calls == 1
    assert [event.blocked for event in audit.events] == [True, False]


@pytest.mark.asyncio
async def test_blocked_batch_marks_every_block_as_not_executed(scanner: ToolSafetyScanner):
    """Audit events reflect that no block is delegated when any block is denied."""
    delegate = RecordingExecutor()
    audit = MemoryAuditSink()
    guarded = SafetyGuardedCodeExecutor(executor=delegate, scanner=scanner, audit_sink=audit)

    await guarded.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="print('safe')"),
            CodeBlock(language="bash", code="rm -rf /tmp/data"),
        ]),
    )

    assert delegate.calls == 0
    assert len(audit.events) == 2
    assert all(event.blocked for event in audit.events)


@pytest.mark.asyncio
async def test_code_executor_wrapper_enforces_output_limit(scanner: ToolSafetyScanner):
    """Allowed executor output is truncated to the policy byte limit."""
    delegate = RecordingExecutor(output="x" * 256)
    guarded = SafetyGuardedCodeExecutor(executor=delegate, scanner=scanner)

    result = await guarded.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert len(result.output.encode("utf-8")) <= scanner.policy.max_output_bytes
    assert result.output.endswith("[tool safety output truncated]")


@pytest.mark.asyncio
async def test_code_executor_wrapper_enforces_tiny_output_limit():
    """The byte limit holds even when it is shorter than the truncation marker."""
    delegate = RecordingExecutor(output="x" * 256)
    scanner = ToolSafetyScanner(ToolSafetyPolicy(max_output_bytes=8))
    guarded = SafetyGuardedCodeExecutor(executor=delegate, scanner=scanner)

    result = await guarded.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert len(result.output.encode("utf-8")) <= 8


@pytest.mark.asyncio
async def test_filter_sets_opentelemetry_attributes(scanner: ToolSafetyScanner):
    """Safety decisions are attached to the active OpenTelemetry span."""
    span = MagicMock()

    def run_script(command: str):
        return {"command": command}

    tool = FunctionTool(
        run_script,
        filters=[ToolScriptSafetyFilter(scanner=scanner, audit_sink=MemoryAuditSink())],
    )
    with patch("trpc_agent_sdk.tools.safety._telemetry.trace.get_current_span", return_value=span):
        await tool.run_async(tool_context=_tool_context(), args={"command": "rm -rf /tmp/data"})

    span.set_attribute.assert_any_call("tool.safety.decision", "deny")
    span.set_attribute.assert_any_call("tool.safety.risk_level", "critical")
    span.set_attribute.assert_any_call("tool.safety.rule_id", "FILE-001")
    span.set_attribute.assert_any_call("tool.safety.blocked", True)


def test_jsonl_audit_sink_writes_monitoring_event(scanner: ToolSafetyScanner, tmp_path: Path):
    """The JSONL sink writes the required monitoring and audit fields."""
    path = tmp_path / "audit.jsonl"
    report = scanner.scan_command("rm -rf /tmp/data", tool_name="Bash")
    sink = JsonlAuditSink(path)
    sink.emit(report.to_audit_event(blocked=True))

    event = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "tool_name",
        "decision",
        "risk_level",
        "rule_id",
        "rule_ids",
        "duration_ms",
        "redacted",
        "blocked",
    }
    assert required.issubset(event)
    assert event["tool_name"] == "Bash"
    assert event["blocked"] is True
    assert event["rule_id"] == "FILE-001"
