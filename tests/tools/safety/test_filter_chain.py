# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for ToolSafetyFilter chain behavior and interaction with other filters."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools.safety._filter import ToolSafetyFilter
from trpc_agent_sdk.tools.safety._filter import add_tool_safety_filter
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import SafetyFinding
from trpc_agent_sdk.tools.safety._types import RiskType
from trpc_agent_sdk.tools.safety._types import SafetyReport
from trpc_agent_sdk.tools.safety._types import ScanTarget
from trpc_agent_sdk.tools.safety._types import ScriptLanguage


class TestFilterBlocksAndStops:

    def test_deny_stops_filter_chain(self):
        """When scanner returns DENY, rsp.is_continue is set to False."""
        f = ToolSafetyFilter()
        rsp = FilterResult()
        critical_finding = SafetyFinding(
            rule_id="R001_TEST", rule_name="T",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf /", recommendation="block",
        )
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
                    findings=[critical_finding],
                    summary="Blocked.",
                )
                asyncio.run(f._before(None, {"command": "rm -rf /"}, rsp))
        assert rsp.is_continue is False
        assert rsp.rsp["blocked"] is True
        assert rsp.rsp["decision"] == "deny"

    def test_review_with_block_on_review_stops_chain(self):
        """When block_on_review=True, NEEDS_HUMAN_REVIEW also stops the chain."""
        f = ToolSafetyFilter(block_on_review=True)
        rsp = FilterResult()
        medium_finding = SafetyFinding(
            rule_id="R004_TEST", rule_name="T",
            risk_type=RiskType.DEPENDENCY_INSTALL,
            risk_level=RiskLevel.MEDIUM,
            evidence="pip install x", recommendation="review",
        )
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
                    findings=[medium_finding],
                    summary="Review required.",
                )
                asyncio.run(f._before(None, {"command": "pip install x"}, rsp))
        assert rsp.is_continue is False
        assert rsp.rsp["decision"] == "needs_human_review"

    def test_allow_passes_through(self):
        """When scanner returns ALLOW, rsp.is_continue remains True."""
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
                    summary="Passed.",
                )
                asyncio.run(f._before(None, {"command": "echo hi"}, rsp))
        assert rsp.is_continue is True


class TestFilterInstancesAreIndependent:

    def test_each_tool_gets_own_filter(self):
        """add_tool_safety_filter creates independent instances per tool."""
        t1 = MagicMock()
        t2 = MagicMock()
        t1.filters = []
        t2.filters = []
        add_tool_safety_filter([t1, t2], block_on_review=True)
        assert len(t1.filters) == 1
        assert len(t2.filters) == 1
        assert t1.filters[0] is not t2.filters[0]
        assert isinstance(t1.filters[0], ToolSafetyFilter)
        assert isinstance(t2.filters[0], ToolSafetyFilter)
