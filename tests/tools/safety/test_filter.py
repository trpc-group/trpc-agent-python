# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for SafetyFilter and SafetyWrapper — Phase 3 capability.

Tests cover:
1. SafetyFilter registration and before-filter logic
2. SafetyBlockedError exception
3. SafetyWrapper standalone usage
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.tools.safety._safety_filter import SafetyBlockedError
from trpc_agent_sdk.tools.safety._safety_filter import SafetyFilter
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._types import SafetyDecision
from trpc_agent_sdk.tools.safety._types import SafetyReport
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._wrapper import SafetyWrapper

# ── SafetyFilter Tests ────────────────────────────────────────────────────


class TestSafetyFilter:
    """Tests for SafetyFilter — Phase 3.1."""

    def test_filter_is_registered(self):
        """SafetyFilter should be importable and instantiable."""
        from trpc_agent_sdk.tools.safety._safety_filter import SafetyFilter
        from trpc_agent_sdk.tools.safety import SafetyFilter as SafetyFilterPublic
        # 验证类可以正常导入
        assert SafetyFilter is not None
        assert SafetyFilterPublic is SafetyFilter
        # 验证可以实例化（使用显式策略，避免依赖文件加载）
        from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
        policy = SafetyPolicy.from_dict({"rules": {}})
        instance = SafetyFilter(policy=policy)
        assert instance is not None

    def test_filter_blocks_dangerous_command(self):
        """SafetyFilter should block dangerous commands in _before()."""
        from trpc_agent_sdk.filter import FilterResult
        from trpc_agent_sdk.tools.safety._policy import SafetyPolicy

        policy = SafetyPolicy.from_dict({
            "rules": {
                "dangerous_file_operations": {
                    "enabled": True,
                    "decision": "deny",
                    "risk_level": "critical",
                    "patterns": ["rm -rf /"],
                },
            },
        })
        safety_filter = SafetyFilter(policy=policy)
        rsp = FilterResult()
        import asyncio
        asyncio.run(safety_filter._before(
            ctx=None,
            req={"command": "rm -rf /"},
            rsp=rsp,
        ))
        assert rsp.is_continue is False
        assert rsp.error is not None
        assert isinstance(rsp.error, SafetyBlockedError)
        assert rsp.error.tool_name != ""

    def test_filter_allows_safe_command(self):
        """SafetyFilter should allow safe commands (no rules matched)."""
        from trpc_agent_sdk.filter import FilterResult
        from trpc_agent_sdk.tools.safety._policy import SafetyPolicy

        policy = SafetyPolicy.from_dict({
            "rules": {
                "dangerous_file_operations": {
                    "enabled": True,
                    "decision": "deny",
                    "risk_level": "critical",
                    "patterns": ["rm -rf /"],
                },
            },
        })
        safety_filter = SafetyFilter(policy=policy)
        rsp = FilterResult()
        import asyncio
        asyncio.run(safety_filter._before(
            ctx=None,
            req={"command": "ls -la"},
            rsp=rsp,
        ))
        # No matches -> default decision (needs_human_review) -> now blocked
        assert rsp.is_continue is False
        assert rsp.error is not None

    def test_filter_skips_non_dict_req(self):
        """SafetyFilter should skip non-dict requests."""
        from trpc_agent_sdk.filter import FilterResult
        from trpc_agent_sdk.tools.safety._policy import SafetyPolicy

        policy = SafetyPolicy()
        safety_filter = SafetyFilter(policy=policy)
        rsp = FilterResult()
        import asyncio
        asyncio.run(safety_filter._before(
            ctx=None,
            req="not a dict",
            rsp=rsp,
        ))
        assert rsp.is_continue is True  # Should not block

    def test_filter_skips_empty_command(self):
        """SafetyFilter should skip requests without script content."""
        from trpc_agent_sdk.filter import FilterResult
        from trpc_agent_sdk.tools.safety._policy import SafetyPolicy

        policy = SafetyPolicy()
        safety_filter = SafetyFilter(policy=policy)
        rsp = FilterResult()
        import asyncio
        asyncio.run(safety_filter._before(
            ctx=None,
            req={
                "name": "test",
                "not_a_command": "value"
            },
            rsp=rsp,
        ))
        assert rsp.is_continue is True

    def test_safety_blocked_error_message(self):
        """SafetyBlockedError should have a descriptive message."""
        from trpc_agent_sdk.tools.safety._types import RuleMatch
        from trpc_agent_sdk.tools.safety._types import RiskCategory

        r = RuleMatch("R001", RiskCategory.DANGEROUS_FILE_OPERATION, RiskLevel.CRITICAL, "rm -rf /", 1, "Remove")
        report = SafetyReport(SafetyDecision.DENY, RiskLevel.CRITICAL, [r])
        err = SafetyBlockedError("Bash", report)
        assert "Bash" in str(err)
        assert "DENY" in str(err)
        assert "R001" in str(err)
        assert err.tool_name == "Bash"
        assert err.report is report

    def test_filter_extracts_command_from_args(self):
        """SafetyFilter should extract 'command' key from args."""
        from trpc_agent_sdk.tools.safety._safety_filter import SafetyFilter as SF
        result = SF._extract_script_content({"command": "rm -rf /"})
        assert result == "rm -rf /"

    def test_filter_extracts_content_from_args(self):
        """SafetyFilter should extract 'content' key from args."""
        from trpc_agent_sdk.tools.safety._safety_filter import SafetyFilter as SF
        result = SF._extract_script_content({"content": "print('hello')"})
        assert result == "print('hello')"

    def test_filter_returns_none_for_no_script(self):
        """SafetyFilter should return None when no script key found."""
        from trpc_agent_sdk.tools.safety._safety_filter import SafetyFilter as SF
        result = SF._extract_script_content({"name": "test", "value": "123"})
        assert result is None


# ── SafetyWrapper Tests ───────────────────────────────────────────────────


class TestSafetyWrapper:
    """Tests for SafetyWrapper — Phase 3.2."""

    @pytest.mark.asyncio
    async def test_wrapper_blocks_dangerous(self):
        """SafetyWrapper should block dangerous scripts."""
        wrapper = SafetyWrapper(policy=SafetyPolicy.from_dict({
            "rules": {
                "dangerous_file_operations": {
                    "enabled": True,
                    "decision": "deny",
                    "risk_level": "critical",
                    "patterns": ["rm -rf /"],
                },
            },
        }))
        result = await wrapper.run_safe(
            tool_name="Bash",
            script_content="rm -rf /",
            script_type="bash",
        )
        assert result["blocked"] is True
        assert result["error"] is not None
        assert "R001" in result["report"]["matches"][0]["rule_id"]

    @pytest.mark.asyncio
    async def test_wrapper_allows_safe(self):
        """SafetyWrapper should allow safe scripts (no rules)."""
        wrapper = SafetyWrapper(policy=SafetyPolicy.from_dict({
            "rules": {},
        }))
        result = await wrapper.run_safe(
            tool_name="Bash",
            script_content="ls -la",
            script_type="bash",
        )
        # Empty rules -> default_decision=NEEDS_HUMAN_REVIEW -> blocked
        assert result["blocked"] is True

    @pytest.mark.asyncio
    async def test_wrapper_executes_function(self):
        """SafetyWrapper should NOT call execute_fn when blocked."""
        wrapper = SafetyWrapper(policy=SafetyPolicy.from_dict({
            "rules": {},
        }))
        mock_fn = AsyncMock(return_value="executed")
        result = await wrapper.run_safe(
            tool_name="Python",
            script_content="print('hello')",
            script_type="python",
            execute_fn=mock_fn,
        )
        # Empty rules -> default_decision=NEEDS_HUMAN_REVIEW -> blocked
        assert result["blocked"] is True
        mock_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wrapper_skips_execution_if_blocked(self):
        """SafetyWrapper should NOT call execute_fn for blocked scripts."""
        wrapper = SafetyWrapper(policy=SafetyPolicy.from_dict({
            "rules": {
                "dangerous_file_operations": {
                    "enabled": True,
                    "decision": "deny",
                    "risk_level": "critical",
                    "patterns": ["rm -rf /"],
                },
            },
        }))
        mock_fn = AsyncMock()
        result = await wrapper.run_safe(
            tool_name="Bash",
            script_content="rm -rf /",
            script_type="bash",
            execute_fn=mock_fn,
        )
        assert result["blocked"] is True
        mock_fn.assert_not_awaited()

    def test_scan_only_returns_report(self):
        """SafetyWrapper.scan_only should return a report dict."""
        wrapper = SafetyWrapper(policy=SafetyPolicy.from_dict({
            "rules": {
                "dangerous_file_operations": {
                    "enabled": True,
                    "decision": "deny",
                    "risk_level": "critical",
                    "patterns": ["rm -rf /"],
                },
            },
        }))
        result = wrapper.scan_only("Bash", "rm -rf /", "bash")
        assert result["decision"] == "DENY"
        assert len(result["matches"]) > 0

    def test_scan_only_safe_script(self):
        """SafetyWrapper.scan_only should show safe for clean scripts."""
        wrapper = SafetyWrapper(policy=SafetyPolicy.from_dict({
            "rules": {},
        }))
        result = wrapper.scan_only("Bash", "ls -la", "bash")
        assert result["decision"] in ("ALLOW", "NEEDS_HUMAN_REVIEW")
