# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for tool safety wrapper and filter."""

from __future__ import annotations

import json

import pytest

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolSafetyBlockedError
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolScriptScanRequest


class _CodeBlock:

    def __init__(self, code: str):
        self.code = code


@pytest.mark.asyncio
async def test_guard_blocks_before_execute():
    guard = ToolSafetyGuard()
    called = False

    async def execute():
        nonlocal called
        called = True
        return "executed"

    result = await guard.run(ToolScriptScanRequest(script="rm -rf /", language="bash", tool_name="bash"), execute)

    assert result.blocked is True
    assert result.report.decision == Decision.DENY
    assert called is False


@pytest.mark.asyncio
async def test_guard_allows_safe_execute():
    guard = ToolSafetyGuard()

    async def execute():
        return "executed"

    result = await guard.run(ToolScriptScanRequest(script="print('ok')", language="python"), execute)

    assert result.blocked is False
    assert result.result == "executed"


def test_assert_allowed_raises_on_blocked_script():
    guard = ToolSafetyGuard()

    with pytest.raises(ToolSafetyBlockedError):
        guard.assert_allowed(ToolScriptScanRequest(script="rm -rf /", language="bash"))


def test_assert_allowed_returns_report_for_safe_script():
    guard = ToolSafetyGuard()

    report = guard.assert_allowed(ToolScriptScanRequest(script="print('ok')", language="python"))

    assert report.decision == Decision.ALLOW


def test_guard_check_writes_audit_event(tmp_path):
    audit_path = tmp_path / "guard-audit.jsonl"
    guard = ToolSafetyGuard(audit_log_path=audit_path)

    report = guard.check(ToolScriptScanRequest(script="print('ok')", language="python", tool_name="python"))

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert report.decision == Decision.ALLOW
    assert event["tool_name"] == "python"
    assert event["decision"] == "allow"


@pytest.mark.asyncio
async def test_filter_stops_denied_request():
    safety_filter = ToolSafetyFilter()
    result = FilterResult()

    await safety_filter._before(
        None,
        {
            "script": "rm -rf /",
            "language": "bash",
            "tool_name": "bash"
        },
        result,
    )

    assert result.is_continue is False
    assert result.error is not None
    assert result.rsp["decision"] == "deny"


@pytest.mark.asyncio
async def test_filter_writes_audit_event(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    safety_filter = ToolSafetyFilter(audit_log_path=audit_path)
    result = FilterResult()

    await safety_filter._before(
        None,
        {
            "script": "rm -rf /",
            "language": "bash",
            "tool_name": "bash"
        },
        result,
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["tool_name"] == "bash"
    assert event["blocked"] is True
    assert "BASH_RECURSIVE_DELETE" in event["rule_ids"]


@pytest.mark.asyncio
async def test_filter_ignores_non_mapping_request():
    safety_filter = ToolSafetyFilter()
    result = FilterResult()

    await safety_filter._before(None, "print('ok')", result)

    assert result.is_continue is True
    assert result.rsp is None


@pytest.mark.asyncio
async def test_filter_ignores_request_without_script():
    safety_filter = ToolSafetyFilter()
    result = FilterResult()

    await safety_filter._before(None, {"tool_name": "python"}, result)

    assert result.is_continue is True
    assert result.rsp is None


@pytest.mark.asyncio
async def test_filter_extracts_command_as_bash():
    safety_filter = ToolSafetyFilter()
    result = FilterResult()

    await safety_filter._before(None, {"command": "echo ok", "tool_name": "shell_tool"}, result)

    assert result.is_continue is True
    assert result.rsp["decision"] == "allow"
    assert result.rsp["language"] == "bash"


@pytest.mark.asyncio
async def test_filter_extracts_python_code_language():
    safety_filter = ToolSafetyFilter()
    result = FilterResult()

    await safety_filter._before(None, {"python_code": "print('ok')", "tool_name": "custom"}, result)

    assert result.is_continue is True
    assert result.rsp["decision"] == "allow"
    assert result.rsp["language"] == "python"


@pytest.mark.asyncio
async def test_filter_infers_language_from_tool_name():
    safety_filter = ToolSafetyFilter()
    python_result = FilterResult()
    unknown_result = FilterResult()

    await safety_filter._before(None, {"script": "print('ok')", "tool_name": "PythonRunner"}, python_result)
    await safety_filter._before(None, {"script": "print('ok')", "tool_name": "custom"}, unknown_result)

    assert python_result.rsp["language"] == "python"
    assert unknown_result.rsp["language"] == "unknown"


@pytest.mark.asyncio
async def test_filter_extracts_code_blocks_from_dicts_and_objects():
    safety_filter = ToolSafetyFilter()
    result = FilterResult()

    await safety_filter._before(
        None,
        {
            "code_blocks": [
                {
                    "code": "print('ok')"
                },
                _CodeBlock("rm -rf /"),
            ],
            "tool_name": "bash",
        },
        result,
    )

    assert result.is_continue is False
    assert result.rsp["decision"] == "deny"
    assert any(finding["rule_id"] == "BASH_RECURSIVE_DELETE" for finding in result.rsp["findings"])


@pytest.mark.asyncio
async def test_filter_scans_command_args_and_context():
    safety_filter = ToolSafetyFilter()
    result = FilterResult()

    await safety_filter._before(
        None,
        {
            "script": "echo ok",
            "args": ["rm", "-rf", "/"],
            "cwd": ".",
            "env": {
                "API_KEY": "secret"
            },
            "tool_metadata": {
                "timeout": "not-a-number"
            },
            "tool_name": "bash",
        },
        result,
    )

    assert result.is_continue is False
    assert result.rsp["sanitized"] is True
    assert any(finding["rule_id"] == "BASH_RECURSIVE_DELETE" for finding in result.rsp["findings"])
