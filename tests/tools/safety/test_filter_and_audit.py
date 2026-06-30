# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for tool safety filter, audit, and executor wrapper."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from opentelemetry import trace

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyFilter


class DummyCodeExecutor(BaseCodeExecutor):
    calls: int = 0

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        self.calls += 1
        return create_code_execution_result(stdout="ok")


def _mock_invocation_context() -> InvocationContext:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent = MagicMock()
    ctx.agent.parallel_tool_calls = False
    ctx.agent.before_tool_callback = None
    ctx.agent.after_tool_callback = None
    ctx.agent_context = AgentContext()
    return ctx


async def test_filter_blocks_dangerous_tool_and_writes_audit(tmp_path):
    def run_command(command: str) -> dict:
        return {"ran": command}

    audit_path = tmp_path / "audit.jsonl"
    policy = SafetyPolicy(audit_log_path=str(audit_path))
    tool = FunctionTool(run_command, filters=[ToolSafetyFilter(policy=policy)])

    result = await tool.run_async(tool_context=_mock_invocation_context(), args={"command": "rm -rf /tmp/out"})

    assert result["error"] == "TOOL_SAFETY_BLOCKED"
    report = result["safety_report"]
    assert report["decision"] == SafetyDecision.DENY.value
    assert "FILE_RECURSIVE_DELETE" in report["findings"][0]["rule_id"]

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["tool_name"] == "run_command"
    assert event["blocked"] is True
    assert event["decision"] == SafetyDecision.DENY.value


async def test_filter_allows_safe_tool(tmp_path):
    def run_command(command: str) -> dict:
        return {"ran": command}

    policy = SafetyPolicy(audit_log_path=str(tmp_path / "audit.jsonl"))
    tool = FunctionTool(run_command, filters=[ToolSafetyFilter(policy=policy)])

    result = await tool.run_async(tool_context=_mock_invocation_context(), args={"command": "echo hello"})

    assert result == {"ran": "echo hello"}


def test_filter_writes_otel_span_attributes(monkeypatch, tmp_path):
    span = MagicMock()
    monkeypatch.setattr(trace, "get_current_span", lambda: span)
    filter_instance = ToolSafetyFilter(policy=SafetyPolicy(audit_log_path=str(tmp_path / "audit.jsonl")))
    tool = MagicMock()
    tool.name = "exec"

    async def run_filter():
        from trpc_agent_sdk.abc import FilterResult
        from trpc_agent_sdk.tools._context_var import reset_tool_var
        from trpc_agent_sdk.tools._context_var import set_tool_var

        token = set_tool_var(tool)
        try:
            rsp = FilterResult()
            await filter_instance._before(AgentContext(), {"command": "rm -rf /tmp/out"}, rsp)
            return rsp
        finally:
            reset_tool_var(token)

    import asyncio

    rsp = asyncio.run(run_filter())
    assert rsp.is_continue is False
    span.set_attribute.assert_any_call("tool.safety.decision", SafetyDecision.DENY.value)
    span.set_attribute.assert_any_call("tool.safety.blocked", True)


async def test_code_executor_wrapper_blocks_before_delegate(tmp_path):
    delegate = DummyCodeExecutor()
    executor = SafetyGuardedCodeExecutor(
        delegate=delegate,
        policy=SafetyPolicy(audit_log_path=str(tmp_path / "audit.jsonl")),
    )

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code='open(".env").read()')]),
    )

    assert "TOOL_SAFETY_BLOCKED" in result.output
    assert delegate.calls == 0


async def test_code_executor_wrapper_delegates_safe_code(tmp_path):
    delegate = DummyCodeExecutor()
    executor = SafetyGuardedCodeExecutor(
        delegate=delegate,
        policy=SafetyPolicy(audit_log_path=str(tmp_path / "audit.jsonl")),
    )
    input_data = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('ok')")])

    result = await executor.execute_code(MagicMock(spec=InvocationContext), input_data)

    assert "ok" in result.output
    assert delegate.calls == 1
