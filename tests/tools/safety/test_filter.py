# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for ToolSafetyFilter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools.safety._filter import ToolSafetyFilter
from trpc_agent_sdk.tools.safety._filter import add_tool_safety_filter
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import SafetyReport
from trpc_agent_sdk.tools.safety._types import ScanTarget
from trpc_agent_sdk.tools.safety._types import ScriptLanguage


class TestToolSafetyFilterBefore:

    def test_blocks_dangerous(self):
        f = ToolSafetyFilter()
        rsp = FilterResult()
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan") as mock_scan:
                mock_scan.return_value = SafetyReport(
                    tool_name="test",
                    decision=Decision.DENY,
                    risk_level=RiskLevel.CRITICAL,
                    blocked=True,
                    sanitized=False,
                    duration_ms=1,
                    language=ScriptLanguage.BASH,
                    target=ScanTarget.TOOL,
                    summary="Blocked.",
                )
                asyncio.run(f._before(None, {"command": "rm -rf /"}, rsp))
        assert rsp.is_continue is False
        assert rsp.rsp["blocked"] is True
        assert rsp.rsp["decision"] == "deny"

    def test_allows_safe(self):
        f = ToolSafetyFilter()
        rsp = FilterResult()
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan") as mock_scan:
                mock_scan.return_value = SafetyReport(
                    tool_name="test",
                    decision=Decision.ALLOW,
                    risk_level=RiskLevel.LOW,
                    blocked=False,
                    sanitized=False,
                    duration_ms=1,
                    language=ScriptLanguage.BASH,
                    target=ScanTarget.TOOL,
                )
                asyncio.run(f._before(None, {"command": "echo hi"}, rsp))
        assert rsp.is_continue is True

    def test_review_blocks_when_enabled(self):
        f = ToolSafetyFilter(block_on_review=True)
        rsp = FilterResult()
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan") as mock_scan:
                mock_scan.return_value = SafetyReport(
                    tool_name="test",
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                    risk_level=RiskLevel.MEDIUM,
                    blocked=False,
                    sanitized=False,
                    duration_ms=1,
                    language=ScriptLanguage.BASH,
                    target=ScanTarget.TOOL,
                )
                asyncio.run(f._before(None, {"command": "pip install x"}, rsp))
        assert rsp.is_continue is False

    def test_review_passes_when_disabled(self):
        f = ToolSafetyFilter(block_on_review=False)
        rsp = FilterResult()
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan") as mock_scan:
                mock_scan.return_value = SafetyReport(
                    tool_name="test",
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                    risk_level=RiskLevel.MEDIUM,
                    blocked=False,
                    sanitized=False,
                    duration_ms=1,
                    language=ScriptLanguage.BASH,
                    target=ScanTarget.TOOL,
                )
                asyncio.run(f._before(None, {"command": "pip install x"}, rsp))
        assert rsp.is_continue is True

    def test_skips_non_executable(self):
        f = ToolSafetyFilter()
        rsp = FilterResult()
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="WeatherTool")
            asyncio.run(f._before(None, {"city": "Tokyo"}, rsp))
        assert rsp.is_continue is True

    def test_scanner_error_denies(self):
        f = ToolSafetyFilter()
        rsp = FilterResult()
        f._audit = MagicMock()
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan", side_effect=RuntimeError("boom")):
                asyncio.run(f._before(None, {"command": "ls"}, rsp))
        assert rsp.is_continue is False
        assert rsp.rsp["decision"] == "deny"
        recorded_report = f._audit.record.call_args[0][0]
        assert recorded_report.rule_ids == ["SAFETY_SCANNER_ERROR"]
        assert [finding.rule_id for finding in recorded_report.findings] == ["SAFETY_SCANNER_ERROR"]

    def test_run_short_circuits_on_deny(self):
        """BaseFilter.run honors ToolSafetyFilter's mutated FilterResult."""
        f = ToolSafetyFilter()
        handle = AsyncMock(return_value={"success": True})
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan") as mock_scan:
                mock_scan.return_value = SafetyReport(
                    tool_name="test",
                    decision=Decision.DENY,
                    risk_level=RiskLevel.CRITICAL,
                    blocked=False,
                    sanitized=False,
                    duration_ms=1,
                    language=ScriptLanguage.BASH,
                    target=ScanTarget.TOOL,
                    rule_ids=["R001_BASH_RECURSIVE_DELETE"],
                    summary="Blocked.",
                )
                result = asyncio.run(f.run(None, {"command": "rm -rf /"}, handle))
        assert handle.await_count == 0
        assert result.is_continue is False
        assert result.rsp["blocked"] is True
        assert result.rsp["decision"] == "deny"

    def test_block_on_review_sets_audit_blocked_true(self):
        """When block_on_review=True and decision=NEEDS_HUMAN_REVIEW, audit records blocked=True."""
        f = ToolSafetyFilter(block_on_review=True)
        rsp = FilterResult()
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            mock_tool.return_value.name = "Bash"
            with patch.object(f, "_audit") as mock_audit:
                with patch.object(f._scanner, "scan") as mock_scan:
                    mock_scan.return_value = SafetyReport(
                        tool_name="test",
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        risk_level=RiskLevel.MEDIUM,
                        blocked=False,
                        sanitized=False,
                        duration_ms=1,
                        language=ScriptLanguage.BASH,
                        target=ScanTarget.TOOL,
                        summary="Review needed.",
                    )
                    asyncio.run(f._before(None, {"command": "pip install x"}, rsp))
        assert mock_audit.record.called
        recorded_report = mock_audit.record.call_args[0][0]
        assert recorded_report.blocked is True


class TestAddToolSafetyFilter:

    def test_each_tool_gets_own_instance(self):
        """add_tool_safety_filter calls add_one_filter on each tool."""
        t1, t2 = MagicMock(), MagicMock()
        add_tool_safety_filter([t1, t2], block_on_review=True)
        t1.add_one_filter.assert_called_once()
        t2.add_one_filter.assert_called_once()
        # Each tool receives a distinct filter instance
        f1 = t1.add_one_filter.call_args[0][0]
        f2 = t2.add_one_filter.call_args[0][0]
        assert f1 is not f2

    def test_real_bash_tool_accepts_filter(self):
        """add_tool_safety_filter on real BashTool must not raise AttributeError."""
        from trpc_agent_sdk.tools import BashTool
        tool = BashTool(enable_safety_guard=False)
        add_tool_safety_filter([tool], block_on_review=True)
        assert any(f.name == "tool_safety" for f in tool.filters)

    def test_real_bash_tool_dedup_second_call(self):
        """Second call to add_tool_safety_filter is a no-op via name dedup."""
        from trpc_agent_sdk.tools import BashTool
        tool = BashTool(enable_safety_guard=False)
        add_tool_safety_filter([tool])
        count = len([f for f in tool.filters if f.name == "tool_safety"])
        add_tool_safety_filter([tool])
        count2 = len([f for f in tool.filters if f.name == "tool_safety"])
        assert count == count2 == 1
