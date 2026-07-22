# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for direct safety guard integration in core execution paths."""

from __future__ import annotations

import json
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.types import Outcome


@pytest.mark.asyncio
async def test_bash_tool_blocks_denied_command_before_execution(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    tool = BashTool(cwd=str(tmp_path), safety_audit_log_path=str(audit_path), enable_safety_guard=True)

    result = await tool._run_async_impl(
        tool_context=Mock(spec=InvocationContext),
        args={"command": "rm -rf /"},
    )

    assert result["success"] is False
    assert result["return_code"] == -1
    assert result["safety_report"]["decision"] == "deny"
    assert result["safety_report"]["blocked"] is True

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_event["tool_name"] == "Bash"
    assert audit_event["blocked"] is True


@pytest.mark.asyncio
async def test_bash_tool_allowed_review_command_reports_not_blocked(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    tool = BashTool(cwd=str(tmp_path), safety_audit_log_path=str(audit_path), enable_safety_guard=True)

    result = await tool._run_async_impl(
        tool_context=Mock(spec=InvocationContext),
        args={"command": "echo test > safety_review.txt"},
    )

    assert result["success"] is True
    assert result["safety_report"]["decision"] == "needs_human_review"
    assert result["safety_report"]["blocked"] is False

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_event["decision"] == "needs_human_review"
    assert audit_event["blocked"] is False


@pytest.mark.asyncio
@patch("trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command")
async def test_unsafe_local_code_executor_blocks_denied_code_before_execution(mock_async_execute, tmp_path):
    executor = UnsafeLocalCodeExecutor(work_dir=str(tmp_path), enable_safety_guard=True)
    code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="rm -rf /")])

    result = await executor.execute_code(Mock(spec=InvocationContext), code_input)

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert "blocked by safety guard" in result.output
    assert "BASH_RECURSIVE_DELETE" in result.output
    mock_async_execute.assert_not_called()


@pytest.mark.asyncio
@patch("trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command")
async def test_unsafe_local_code_executor_default_keeps_existing_execution_path(mock_async_execute, tmp_path):
    from trpc_agent_sdk.utils import CommandExecResult

    mock_async_execute.return_value = CommandExecResult(
        stdout="legacy output",
        stderr="",
        exit_code=0,
        is_timeout=False,
    )
    executor = UnsafeLocalCodeExecutor(work_dir=str(tmp_path))
    code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="rm -rf /")])

    result = await executor.execute_code(Mock(spec=InvocationContext), code_input)

    assert result.outcome == Outcome.OUTCOME_OK
    assert "legacy output" in result.output
    mock_async_execute.assert_called_once()


@pytest.mark.asyncio
@patch("trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command")
async def test_unsafe_local_code_executor_block_on_review_allows_safe_python(mock_async_execute, tmp_path):
    from trpc_agent_sdk.utils import CommandExecResult

    mock_async_execute.return_value = CommandExecResult(
        stdout="safe output",
        stderr="",
        exit_code=0,
        is_timeout=False,
    )
    executor = UnsafeLocalCodeExecutor(work_dir=str(tmp_path), enable_safety_guard=True, block_on_review=True)
    code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe output')")])

    result = await executor.execute_code(Mock(spec=InvocationContext), code_input)

    assert result.outcome == Outcome.OUTCOME_OK
    assert "safe output" in result.output
    mock_async_execute.assert_called_once()


@pytest.mark.asyncio
@patch("trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command")
async def test_unsafe_local_code_executor_allowed_review_code_reports_not_blocked(mock_async_execute, tmp_path):
    from trpc_agent_sdk.utils import CommandExecResult

    audit_path = tmp_path / "audit.jsonl"
    mock_async_execute.return_value = CommandExecResult(
        stdout="reviewed output",
        stderr="",
        exit_code=0,
        is_timeout=False,
    )
    executor = UnsafeLocalCodeExecutor(
        work_dir=str(tmp_path),
        safety_audit_log_path=str(audit_path),
        enable_safety_guard=True,
    )
    code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="import os\nos.system('ls')")])

    result = await executor.execute_code(Mock(spec=InvocationContext), code_input)

    assert result.outcome == Outcome.OUTCOME_OK
    assert "reviewed output" in result.output
    mock_async_execute.assert_called_once()

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_event["decision"] == "needs_human_review"
    assert audit_event["blocked"] is False
