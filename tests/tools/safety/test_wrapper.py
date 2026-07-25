# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for SafeCodeExecutor."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.code_executors import BaseCodeExecutor as ExecBase
from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import SafetyReport
from trpc_agent_sdk.tools.safety._types import ScanTarget
from trpc_agent_sdk.tools.safety._types import ScriptLanguage


class _FakeExecutor(ExecBase):

    def __init__(self):
        super().__init__()
        object.__setattr__(self, 'called', False)

    async def execute_code(self, inv_ctx, inp):
        object.__setattr__(self, 'called', True)
        return MagicMock()


class _MockScanner:

    def __init__(self, policy=None):
        pass

    def scan(self, req):
        return SafetyReport(
            tool_name="test",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=1,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.CODE_EXECUTOR,
        )


class TestSafeCodeExecutor:

    @patch("trpc_agent_sdk.tools.safety._wrapper.SafetyScanner", _MockScanner)
    def test_safe_code_delegates(self):
        from trpc_agent_sdk.tools.safety._wrapper import SafeCodeExecutor

        inner = _FakeExecutor()
        exe = SafeCodeExecutor(inner_executor=inner, tool_name="test")

        block = CodeBlock(language="python", code="print('hello')")
        inp = CodeExecutionInput(code_blocks=[block], execution_id="1")

        asyncio.run(exe.execute_code(MagicMock(), inp))
        assert inner.called is True

    def test_aggregate_decision_blocks_on_deny(self):
        """Verify aggregate_decision with a CRITICAL finding blocks execution."""
        from trpc_agent_sdk.tools.safety._types import SafetyFinding, RiskType, aggregate_decision
        finding = SafetyFinding(
            rule_id="R001_TEST", rule_name="T",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL, evidence="e", recommendation="r")
        decision = aggregate_decision([finding])
        assert decision == Decision.DENY

    def test_aggregate_decision_allows_safe(self):
        from trpc_agent_sdk.tools.safety._types import SafetyFinding, RiskType, aggregate_decision
        finding = SafetyFinding(
            rule_id="R001_TEST", rule_name="T",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.LOW, evidence="e", recommendation="r")
        decision = aggregate_decision([finding])
        assert decision == Decision.ALLOW

    @patch("trpc_agent_sdk.tools.safety._wrapper.create_code_execution_result")
    def test_safe_code_executor_blocks_yields_outcome_failed(self, mock_create):
        """When SafetyScanner blocks, create_code_execution_result is called with stderr."""
        from trpc_agent_sdk.tools.safety._wrapper import SafeCodeExecutor
        from trpc_agent_sdk.tools.safety._types import SafetyFinding, RiskType

        inner = _FakeExecutor()
        exe = SafeCodeExecutor(inner_executor=inner, tool_name="test")

        # Mock a DENY scan — findings must contain a CRITICAL finding
        # so that aggregate_decision returns DENY
        critical_finding = SafetyFinding(
            rule_id="R001_BASH_RECURSIVE_DELETE",
            rule_name="Dangerous Delete",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf /",
            recommendation="Do not do this.",
        )
        with patch("trpc_agent_sdk.tools.safety._wrapper.SafetyScanner") as MockScanner:
            mock_scanner = MockScanner.return_value
            mock_scanner.scan.return_value = SafetyReport(
                tool_name="test",
                decision=Decision.DENY,
                risk_level=RiskLevel.CRITICAL,
                blocked=True,
                sanitized=False,
                duration_ms=1,
                language=ScriptLanguage.BASH,
                target=ScanTarget.CODE_EXECUTOR,
                findings=[critical_finding],
            )
            block = MagicMock()
            block.language = "bash"
            block.code = "rm -rf /"
            inp = MagicMock()
            inp.code_blocks = [block]

            asyncio.run(exe.execute_code(MagicMock(), inp))

        # Check that create_code_execution_result was called with stderr
        assert mock_create.called
        call_kwargs = mock_create.call_args.kwargs
        assert "blocked by safety guard" in call_kwargs.get("stderr", "")
        # Inner executor should NOT have been called
        assert inner.called is False


class TestSafetyWrappedToolSet:

    def test_injects_filter_into_each_tool(self):
        """SafetyWrappedToolSet adds ToolSafetyFilter to each tool from inner toolset."""
        from unittest.mock import AsyncMock
        from trpc_agent_sdk.tools.safety._wrapper import SafetyWrappedToolSet

        inner = MagicMock()
        inner.name = "test_ts"
        mock_tool_a, mock_tool_b = MagicMock(), MagicMock()
        mock_tool_a.filters = []
        mock_tool_b.filters = []
        inner.get_tools = AsyncMock(return_value=[mock_tool_a, mock_tool_b])

        wrapped = SafetyWrappedToolSet(inner=inner, block_on_review=True)
        tools = asyncio.run(wrapped.get_tools())

        assert len(tools) == 2
        assert len(mock_tool_a.filters) == 1
        assert len(mock_tool_b.filters) == 1
        # Each tool gets independent filter
        assert mock_tool_a.filters[0] is not mock_tool_b.filters[0]

    def test_close_delegates_to_inner(self):
        """SafetyWrappedToolSet.close() delegates to inner toolset."""
        from unittest.mock import AsyncMock
        from trpc_agent_sdk.tools.safety._wrapper import SafetyWrappedToolSet

        inner = MagicMock()
        inner.close = AsyncMock()
        wrapped = SafetyWrappedToolSet(inner=inner)

        asyncio.run(wrapped.close())
        inner.close.assert_called_once()
