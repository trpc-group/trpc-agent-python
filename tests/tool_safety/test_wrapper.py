# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under Apache-2.0.
"""Tests for the wrapper helpers (wrap_tool / SafeCodeExecutor).

Issue criterion 7 explicitly requires: "Filter / wrapper must be able to block
high-risk scripts before execution and record one auditable event." The
filter path is covered by test_tool_filter.py; this module covers the wrapper
path so that the wrapper half of criterion 7 is also verified.

These tests avoid importing heavy SDK sub-packages (tools.file_tools needs
``anthropic``; code_executors needs ``docker``) that may be absent in a
minimal install. ``wrap_tool`` is exercised against a lightweight fake tool;
``SafeCodeExecutor`` is exercised only when ``trpc_agent_sdk.code_executors``
is importable (i.e. the docker optional dependency is installed), and skipped
otherwise — mirroring the lazy-import strategy used inside wrapper.py itself.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from examples.tool_safety.safety import PolicyConfig
from examples.tool_safety.safety import SafeCodeExecutor
from examples.tool_safety.safety import wrap_tool
from examples.tool_safety.safety import safety_wrapper
from examples.tool_safety.safety import SafetyDeniedError
from examples.tool_safety.safety import SafetyReviewedSkillRunner
from examples.tool_safety.safety import _SDK_AVAILABLE

# FilterResult is a lightweight ABC; it does not pull the heavy model/tool
# dependency tree and is safe to import in a minimal install.
try:
    from trpc_agent_sdk.abc import FilterResult
except Exception:  # pylint: disable=broad-except
    FilterResult = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    not _SDK_AVAILABLE or FilterResult is None,
    reason="tRPC-Agent SDK core (abc.FilterResult) not importable",
)


def _policy(tmp_path: Path) -> PolicyConfig:
    """Minimal policy: block .env access and all network egress."""
    return PolicyConfig(whitelisted_domains=[], forbidden_paths=[".env"])


# ---------------------------------------------------------------------------
# Fake tool — stands in for BashTool without pulling the anthropic dep chain.
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal stand-in for a BaseTool.

    ``wrap_tool`` only needs ``.name`` and ``.add_one_filter``, so we provide
    just those. The filter list is stored so tests can drive the filter's
    ``_before`` hook directly.
    """

    def __init__(self, name: str = "fake_bash") -> None:
        self.name = name
        self.filters: list[Any] = []

    def add_one_filter(self, flt: Any, *, force: bool = False) -> None:
        self.filters.append(flt)


# ---------------------------------------------------------------------------
# wrap_tool
# ---------------------------------------------------------------------------


def test_wrap_tool_blocks_dangerous_bash(tmp_path: Path):
    """wrap_tool must attach the filter so dangerous bash is denied + audited."""
    tool = _FakeTool(name="Bash")
    wrapped = wrap_tool(tool, _policy(tmp_path), audit_path=str(tmp_path / "audit.jsonl"))

    # The safety filter must be present on the wrapped tool.
    assert len(wrapped.filters) == 1
    flt = wrapped.filters[0]
    assert getattr(flt, "_name", None) == "tool_safety_filter"

    # Drive the filter's _before hook (same hook the SDK filter chain calls).
    rsp = FilterResult()
    req = {"command": "rm -rf / && cat ~/.ssh/id_rsa"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access

    assert rsp.is_continue is False
    assert rsp.rsp["error"] == "TOOL_SAFETY_DENY"

    # Audit record must be written (criterion 7: "record one auditable event").
    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True
    assert rec["tool_name"] == "Bash"


def test_wrap_tool_allows_safe_bash(tmp_path: Path):
    """wrap_tool must NOT block safe commands."""
    tool = _FakeTool(name="Bash")
    wrapped = wrap_tool(tool, _policy(tmp_path))

    rsp = FilterResult()
    req = {"command": "ls -la"}
    asyncio.run(wrapped.filters[0]._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is True


# ---------------------------------------------------------------------------
# SafeCodeExecutor — only runs when trpc_agent_sdk.code_executors is importable.
# ---------------------------------------------------------------------------


def _try_import_code_executors():
    """Return (CodeExecutionInput, CodeExecutionResult) or (None, None)."""
    try:
        from trpc_agent_sdk.code_executors import CodeExecutionInput
        from trpc_agent_sdk.code_executors import CodeExecutionResult as CER
        return CodeExecutionInput, CER
    except Exception:  # pylint: disable=broad-except
        return None, None


class _FakeInnerExecutor:
    """Minimal stand-in for a real BaseCodeExecutor.

    Using a fake avoids pulling docker / subprocess dependencies into the test
    while still exercising the SafeCodeExecutor wrapper's scan-then-delegate
    logic. ``calls`` records whether delegation happened.
    """

    def __init__(self, cer_cls: Any) -> None:
        self._cer_cls = cer_cls
        self.calls: list[Any] = []

    async def execute_code(self, invocation_context, input_data):
        self.calls.append(input_data)
        return self._cer_cls(stdout="ok", stderr="", exit_code=0)


def test_safe_code_executor_blocks_dangerous_python(tmp_path: Path):
    """SafeCodeExecutor must block `os.system('rm -rf /')` before delegation."""
    CodeExecutionInput, CER = _try_import_code_executors()
    if CodeExecutionInput is None:
        pytest.skip("trpc_agent_sdk.code_executors not importable (docker optional dep missing)")

    inner = _FakeInnerExecutor(CER)
    safe = SafeCodeExecutor(inner, _policy(tmp_path), audit_path=str(tmp_path / "audit.jsonl"))

    code = "import os\nos.system('rm -rf /')"
    inp = CodeExecutionInput(code=code)
    result = asyncio.run(safe.execute_code(None, inp))

    # Blocked: inner must NOT have been called.
    assert inner.calls == []
    # Result carries the deny marker.
    assert "TOOL_SAFETY_DENY" in result.stderr
    assert result.exit_code == 126

    # Audit record must be written.
    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True


def test_safe_code_executor_allows_safe_python(tmp_path: Path):
    """SafeCodeExecutor must delegate safe code to the inner executor."""
    CodeExecutionInput, CER = _try_import_code_executors()
    if CodeExecutionInput is None:
        pytest.skip("trpc_agent_sdk.code_executors not importable (docker optional dep missing)")

    inner = _FakeInnerExecutor(CER)
    safe = SafeCodeExecutor(inner, _policy(tmp_path))

    code = "print('hello world')"
    inp = CodeExecutionInput(code=code)
    result = asyncio.run(safe.execute_code(None, inp))

    # Delegated: inner must have been called exactly once.
    assert len(inner.calls) == 1
    assert result.stdout == "ok"
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# safety_wrapper decorator
# ---------------------------------------------------------------------------


def test_safety_wrapper_blocks_dangerous_script(tmp_path: Path):
    """safety_wrapper must raise SafetyDeniedError on dangerous input."""
    policy = PolicyConfig(forbidden_paths=[".env"])

    @safety_wrapper(tool_name="deco_test", policy=policy,
                    audit_path=str(tmp_path / "audit.jsonl"))
    async def run_script(*, script: str = ""):
        return "executed"

    # Dangerous script: rm -rf / — must raise.
    with pytest.raises(SafetyDeniedError):
        asyncio.run(run_script(script="rm -rf /"))

    # Audit must be written.
    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True


def test_safety_wrapper_allows_safe_script(tmp_path: Path):
    """safety_wrapper must pass safe scripts through to the function."""
    @safety_wrapper(tool_name="deco_safe", policy=PolicyConfig())
    async def run_script(*, script: str = ""):
        return "executed"

    result = asyncio.run(run_script(script="print('hello')"))
    assert result == "executed"


def test_safety_wrapper_sync_function(tmp_path: Path):
    """safety_wrapper must also work on synchronous functions."""
    @safety_wrapper(tool_name="deco_sync", policy=PolicyConfig())
    def run_script(*, script: str = ""):
        return "sync_executed"

    result = run_script(script="print('safe')")
    assert result == "sync_executed"


# ---------------------------------------------------------------------------
# SafetyReviewedSkillRunner
# ---------------------------------------------------------------------------


class _FakeSkillRunner:
    """Minimal skill runner with run_async(tool_context=, args=)."""

    def __init__(self):
        self.calls = 0

    async def run_async(self, *, tool_context, args):
        self.calls += 1
        return {"success": True, "result": "skill ran"}


def test_skill_runner_blocks_dangerous_command(tmp_path: Path):
    """SafetyReviewedSkillRunner must block dangerous skill args."""
    runner = _FakeSkillRunner()
    safe = SafetyReviewedSkillRunner(
        runner, PolicyConfig(), audit_path=str(tmp_path / "audit.jsonl"),
        tool_name="skill_run",
    )

    # Args contain a dangerous command.
    args = {"command": "rm -rf /"}
    result = asyncio.run(safe.run(None, args))

    assert result["success"] is False
    assert result["error"] == "SKILL_BLOCKED"
    assert runner.calls == 0  # inner runner must NOT be called

    # Audit written.
    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"


def test_skill_runner_allows_safe_command(tmp_path: Path):
    """SafetyReviewedSkillRunner must delegate safe args to the inner runner."""
    runner = _FakeSkillRunner()
    safe = SafetyReviewedSkillRunner(runner, PolicyConfig(), tool_name="skill_run")

    args = {"command": "echo hello"}
    result = asyncio.run(safe.run(None, args))

    assert result["success"] is True
    assert runner.calls == 1
