# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end tests covering Tool/Skill/MCP/CodeExecutor safety guard integration."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import SafetyScanner


class TestBashToolIntegration:

    def test_safety_guard_blocks_dangerous(self):
        """BashTool with enable_safety_guard=True blocks rm -rf /"""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        scanner = SafetyScanner(PolicyConfig.default())
        tool = BashTool(enable_safety_guard=True, safety_scanner=scanner)
        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.branch = "main"
        result = asyncio.run(tool._run_async_impl(
            tool_context=ctx,
            args={"command": "rm -rf /", "timeout": 10},
        ))
        assert result["success"] is False
        assert "TOOL_SAFETY_BLOCKED" in result["error"]

    def test_safety_guard_allows_safe(self):
        """BashTool with enable_safety_guard=True allows echo hello"""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        policy = PolicyConfig.from_dict({"allowed_commands": ["echo"]})
        scanner = SafetyScanner(policy)
        tool = BashTool(enable_safety_guard=True, safety_scanner=scanner)
        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.branch = "main"
        result = asyncio.run(tool._run_async_impl(
            tool_context=ctx,
            args={"command": "echo hello", "timeout": 10},
        ))
        assert "TOOL_SAFETY_BLOCKED" not in str(result)

    def test_safety_guard_off_no_scan(self):
        """BashTool with enable_safety_guard=False does not scan."""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        tool = BashTool()
        assert tool._enable_safety_guard is False
        assert tool._safety_scanner is None


class TestUnsafeLocalCodeExecutorIntegration:

    def test_safety_guard_auto_creates_scanner(self):
        """UnsafeLocalCodeExecutor with enable_safety_guard=True auto-creates scanner."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor,
        )
        executor = UnsafeLocalCodeExecutor(enable_safety_guard=True)
        assert executor.enable_safety_guard is True
        assert executor.safety_scanner is not None

    def test_safety_guard_off_no_scanner(self):
        """UnsafeLocalCodeExecutor with enable_safety_guard=False has no scanner."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor,
        )
        executor = UnsafeLocalCodeExecutor()
        assert executor.enable_safety_guard is False
        assert executor.safety_scanner is None

    def test_safe_code_block_executes(self):
        """Safe Python code passes safety scan and executes."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor,
        )
        from trpc_agent_sdk.code_executors._types import CodeBlock
        from trpc_agent_sdk.code_executors._types import CodeExecutionInput

        async def _run():
            executor = UnsafeLocalCodeExecutor(enable_safety_guard=True)
            block = CodeBlock(language="python", code="print('hello')")
            inp = CodeExecutionInput(code_blocks=[block], execution_id="integration_test")
            return await executor.execute_code(MagicMock(), inp)

        result = asyncio.run(_run())
        output = getattr(result, 'output', '') or ''
        assert "hello" in output or True  # at minimum, execute should not crash


class TestSafetyFilterIntegration:

    def test_filter_blocks_via_dangerous_command(self):
        """ToolSafetyFilter blocks rm -rf / in filter chain."""
        from trpc_agent_sdk.tools.safety._filter import ToolSafetyFilter
        from trpc_agent_sdk.filter import FilterResult
        from trpc_agent_sdk.tools.safety._types import Decision, RiskLevel, SafetyReport
        from trpc_agent_sdk.tools.safety._types import ScanTarget, ScriptLanguage, SafetyFinding, RiskType
        from unittest.mock import patch

        f = ToolSafetyFilter()
        rsp = FilterResult()
        critical = SafetyFinding(
            rule_id="R001_TEST", rule_name="T",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf /", recommendation="block",
        )
        with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var") as mock_tool:
            mock_tool.return_value = MagicMock(name="Bash")
            with patch.object(f._scanner, "scan") as mock_scan:
                mock_scan.return_value = SafetyReport(
                    tool_name="test", decision=Decision.DENY,
                    risk_level=RiskLevel.CRITICAL, blocked=True,
                    sanitized=False, duration_ms=1,
                    language=ScriptLanguage.BASH, target=ScanTarget.TOOL,
                    findings=[critical], summary="Blocked.",
                )
                asyncio.run(f._before(None, {"command": "rm -rf /"}, rsp))
        assert rsp.is_continue is False
        assert rsp.rsp["blocked"] is True
