# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for opt-in safety guard behavior on BashTool and UnsafeLocalCodeExecutor."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import SafetyScanner


class _CapturingScanner:

    def __init__(self, policy=None):
        self._policy = policy or PolicyConfig.default()
        self.requests = []

    def scan(self, req):
        self.requests.append(req)
        return SafetyReport(
            tool_name=req.tool_name,
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=1,
            language=req.language,
            target=req.target,
        )


class TestBashToolOptIn:

    def test_default_no_safety_guard(self):
        """enable_safety_guard=False (default) preserves existing behavior."""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        tool = BashTool()
        assert tool._enable_safety_guard is False
        assert tool._safety_scanner is None

    def test_enable_safety_guard_auto_creates_scanner(self):
        """enable_safety_guard=True auto-creates SafetyScanner with default policy."""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        tool = BashTool(enable_safety_guard=True)
        assert tool._enable_safety_guard is True
        assert tool._safety_scanner is not None
        assert isinstance(tool._safety_scanner, SafetyScanner)

    def test_safety_scanner_can_be_injected(self):
        """Externally created SafetyScanner with custom policy is accepted."""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        policy = PolicyConfig.from_dict({"max_timeout_seconds": 60})
        scanner = SafetyScanner(policy)
        tool = BashTool(enable_safety_guard=True, safety_scanner=scanner)
        assert tool._safety_scanner is scanner

    def test_blocks_dangerous_command(self):
        """BashTool(enable_safety_guard=True) blocks rm -rf /"""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        scanner = SafetyScanner(PolicyConfig.default())
        tool = BashTool(enable_safety_guard=True, safety_scanner=scanner)
        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.branch = "main"
        result = asyncio.run(tool._run_async_impl(
            tool_context=ctx,
            args={
                "command": "rm -rf /",
                "timeout": 10
            },
        ))
        assert result["success"] is False
        assert "TOOL_SAFETY_BLOCKED" in result["error"]

    def test_allows_safe_command(self):
        """BashTool(enable_safety_guard=True) does not block echo hello."""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        policy = PolicyConfig.from_dict({"allowed_commands": ["echo"]})
        scanner = SafetyScanner(policy)
        tool = BashTool(enable_safety_guard=True, safety_scanner=scanner)
        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.branch = "main"
        result = asyncio.run(tool._run_async_impl(
            tool_context=ctx,
            args={
                "command": "echo hello",
                "timeout": 10
            },
        ))
        assert "TOOL_SAFETY_BLOCKED" not in str(result)

    def test_scan_env_does_not_include_secret_values(self, monkeypatch):
        """BashTool passes env keys to the scanner without copying values."""
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
        scanner = _CapturingScanner()
        monkeypatch.setenv("TRPC_SECRET_TOKEN", "real-secret-value")
        tool = BashTool(enable_safety_guard=True, safety_scanner=scanner)
        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.branch = "main"

        asyncio.run(tool._run_async_impl(
            tool_context=ctx,
            args={
                "command": "echo hello",
                "timeout": 10
            },
        ))

        assert scanner.requests
        assert "TRPC_SECRET_TOKEN" in scanner.requests[0].env
        assert scanner.requests[0].env["TRPC_SECRET_TOKEN"] == ""


class TestUnsafeLocalCodeExecutorOptIn:

    def test_default_no_safety_guard(self):
        """enable_safety_guard=False (default) preserves existing behavior."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        executor = UnsafeLocalCodeExecutor()
        assert executor.enable_safety_guard is False
        assert executor.safety_scanner is None

    def test_enable_safety_guard_auto_creates_scanner(self):
        """enable_safety_guard=True auto-creates SafetyScanner."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        executor = UnsafeLocalCodeExecutor(enable_safety_guard=True)
        assert executor.enable_safety_guard is True
        assert executor.safety_scanner is not None

    def test_safe_code_passes_scan_and_executes(self):
        """Code block that passes safety scan executes normally."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        from trpc_agent_sdk.code_executors._types import CodeBlock
        from trpc_agent_sdk.code_executors._types import CodeExecutionInput

        async def _run():
            policy = PolicyConfig.from_dict({"allowed_commands": []})
            scanner = SafetyScanner(policy)
            executor = UnsafeLocalCodeExecutor(
                enable_safety_guard=True,
                safety_scanner=scanner,
            )
            block = CodeBlock(language="python", code="print('hello')")
            inp = CodeExecutionInput(code_blocks=[block], execution_id="test")
            return await executor.execute_code(MagicMock(), inp)

        result = asyncio.run(_run())
        assert "hello" in getattr(result, 'output', '')

    def test_scan_receives_resolved_work_dir(self):
        """Safety scan uses the temporary execution dir, not the empty config cwd."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        from trpc_agent_sdk.code_executors._types import CodeBlock
        from trpc_agent_sdk.code_executors._types import CodeExecutionInput

        async def _fake_execute(self, work_dir, block, block_index):
            return "ok"

        async def _run():
            scanner = _CapturingScanner()
            executor = UnsafeLocalCodeExecutor(enable_safety_guard=True, safety_scanner=scanner)
            executor._execute_code_block = _fake_execute.__get__(executor, UnsafeLocalCodeExecutor)
            block = CodeBlock(language="python", code="print('hello')")
            inp = CodeExecutionInput(code_blocks=[block], execution_id="ctx")
            result = await executor.execute_code(MagicMock(), inp)
            return scanner, result

        scanner, result = asyncio.run(_run())
        assert "ok" in getattr(result, 'output', '')
        assert scanner.requests
        assert scanner.requests[0].cwd
        assert "codeexec_ctx_" in scanner.requests[0].cwd

    def test_timeout_limit_blocks_when_executor_timeout_exceeds_policy(self):
        """Executor timeout is included in scan metadata and enforced."""
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        from trpc_agent_sdk.code_executors._types import CodeBlock
        from trpc_agent_sdk.code_executors._types import CodeExecutionInput

        async def _run():
            policy = PolicyConfig.from_dict({"max_timeout_seconds": 1})
            scanner = SafetyScanner(policy)
            executor = UnsafeLocalCodeExecutor(enable_safety_guard=True, safety_scanner=scanner, timeout=2)
            block = CodeBlock(language="python", code="print('hello')")
            inp = CodeExecutionInput(code_blocks=[block], execution_id="timeout")
            return await executor.execute_code(MagicMock(), inp)

        result = asyncio.run(_run())
        assert "blocked by safety guard" in getattr(result, 'output', '')
