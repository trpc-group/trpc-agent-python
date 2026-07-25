# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for ToolSafetyFilter."""

from __future__ import annotations

import asyncio
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
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan", side_effect=RuntimeError("boom")):
                asyncio.run(f._before(None, {"command": "ls"}, rsp))
        assert rsp.is_continue is False
        assert rsp.rsp["decision"] == "deny"


class TestAddToolSafetyFilter:

    def test_each_tool_gets_own_instance(self):
        t1, t2 = MagicMock(), MagicMock()
        t1.filters, t2.filters = [], []
        add_tool_safety_filter([t1, t2], block_on_review=True)
        assert len(t1.filters) == 1
        assert len(t2.filters) == 1
        assert t1.filters[0] is not t2.filters[0]
