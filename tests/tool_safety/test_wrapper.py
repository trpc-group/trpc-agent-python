# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under Apache-2.0.
"""Tests for wrapper helpers (wrap_tool / SafeCodeExecutor / decorators)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import SafeCodeExecutor
from trpc_agent_sdk.safety import SafetyDeniedError
from trpc_agent_sdk.safety import SafetyReviewedSkillRunner
from trpc_agent_sdk.safety import _SDK_AVAILABLE
from trpc_agent_sdk.safety import safety_wrapper
from trpc_agent_sdk.safety import wrap_tool

try:
    from trpc_agent_sdk.abc import FilterResult
except Exception:  # pylint: disable=broad-except
    FilterResult = None  # type: ignore[assignment]

try:
    from trpc_agent_sdk.types import Outcome
    _REAL_OUTCOME_AVAILABLE = True
except Exception:  # pylint: disable=broad-except
    Outcome = None  # type: ignore[assignment]
    _REAL_OUTCOME_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _SDK_AVAILABLE or FilterResult is None,
    reason="tRPC-Agent SDK core (abc.FilterResult) not importable",
)


def _policy(tmp_path: Path) -> PolicyConfig:
    return PolicyConfig(whitelisted_domains=[], forbidden_paths=[".env"])


class _FakeTool:

    def __init__(self, name: str = "fake_bash") -> None:
        self.name = name
        self.filters: list[Any] = []

    def add_one_filter(self, flt: Any, *, force: bool = False) -> None:
        self.filters.append(flt)


def test_wrap_tool_blocks_dangerous_bash(tmp_path: Path):
    tool = _FakeTool(name="Bash")
    wrapped = wrap_tool(tool, _policy(tmp_path), audit_path=str(tmp_path / "audit.jsonl"))

    assert len(wrapped.filters) == 1
    flt = wrapped.filters[0]
    assert getattr(flt, "_name", None) == "tool_safety_filter"

    rsp = FilterResult()
    req = {"command": "rm -rf / && cat ~/.ssh/id_rsa"}
    asyncio.run(flt._before(None, req, rsp))  # pylint: disable=protected-access

    assert rsp.is_continue is False
    assert rsp.rsp["error"] == "TOOL_SAFETY_DENY"

    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True
    assert rec["tool_name"] == "Bash"


def test_wrap_tool_allows_safe_bash(tmp_path: Path):
    tool = _FakeTool(name="Bash")
    wrapped = wrap_tool(tool, _policy(tmp_path))

    rsp = FilterResult()
    req = {"command": "ls -la"}
    asyncio.run(wrapped.filters[0]._before(None, req, rsp))  # pylint: disable=protected-access
    assert rsp.is_continue is True


class _SimpleCodeInput:
    """Minimal stand-in for CodeExecutionInput (no code_executors import)."""

    def __init__(self, code: str = "", code_blocks=None, language: str = "python"):
        self.code = code
        self.code_blocks = code_blocks or []
        self.language = language


class _FakeInnerExecutor:

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute_code(self, invocation_context, input_data):
        self.calls.append(input_data)
        # Return a real CodeExecutionResult so the test exercises the same
        # types downstream pipeline code (Part.from_code_execution_result,
        # _openai_model) will see, instead of a stub that hides attr errors.
        from trpc_agent_sdk.types import CodeExecutionResult
        from trpc_agent_sdk.types import Outcome
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="ok")


def test_safe_code_executor_blocks_dangerous_python(tmp_path: Path):
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner, _policy(tmp_path), audit_path=str(tmp_path / "audit.jsonl"))

    code = "import os\nos.system('rm -rf /')"
    inp = _SimpleCodeInput(code=code)
    result = asyncio.run(safe.execute_code(None, inp))

    assert inner.calls == []
    assert "TOOL_SAFETY_DENY" in result.output
    # Real Outcome enum: .value / .name both available. Assert against the
    # enum member directly so the test fails if a stub object is ever
    # reintroduced (stubs lack .value and would fail equality).
    if _REAL_OUTCOME_AVAILABLE:
        assert result.outcome == Outcome.OUTCOME_FAILED
        assert result.outcome.value is not None  # stub would crash here
    else:  # pragma: no cover - fallback when SDK types unavailable
        assert result.outcome.name == "OUTCOME_FAILED"

    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True


def test_safe_code_executor_allows_safe_python(tmp_path: Path):
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner, _policy(tmp_path))

    code = "print('hello world')"
    inp = _SimpleCodeInput(code=code)
    result = asyncio.run(safe.execute_code(None, inp))

    assert len(inner.calls) == 1
    assert "ok" in result.output
    if _REAL_OUTCOME_AVAILABLE:
        assert result.outcome == Outcome.OUTCOME_OK
    else:  # pragma: no cover - fallback when SDK types unavailable
        assert result.outcome.name == "OUTCOME_OK"


def test_safety_wrapper_blocks_dangerous_script(tmp_path: Path):
    policy = PolicyConfig(forbidden_paths=[".env"])

    @safety_wrapper(tool_name="deco_test", policy=policy, audit_path=str(tmp_path / "audit.jsonl"))
    async def run_script(*, script: str = ""):
        return "executed"

    with pytest.raises(SafetyDeniedError):
        asyncio.run(run_script(script="rm -rf /"))

    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True


def test_safety_wrapper_allows_safe_script(tmp_path: Path):

    @safety_wrapper(tool_name="deco_safe", policy=PolicyConfig())
    async def run_script(*, script: str = ""):
        return "executed"

    result = asyncio.run(run_script(script="print('hello')"))
    assert result == "executed"


def test_safety_wrapper_sync_function(tmp_path: Path):

    @safety_wrapper(tool_name="deco_sync", policy=PolicyConfig())
    def run_script(*, script: str = ""):
        return "sync_executed"

    result = run_script(script="print('safe')")
    assert result == "sync_executed"


class _FakeSkillRunner:

    def __init__(self):
        self.calls = 0

    async def run_async(self, *, tool_context, args):
        self.calls += 1
        return {"success": True, "result": "skill ran"}


def test_skill_runner_blocks_dangerous_command(tmp_path: Path):
    runner = _FakeSkillRunner()
    safe = SafetyReviewedSkillRunner(
        runner,
        PolicyConfig(),
        audit_path=str(tmp_path / "audit.jsonl"),
        tool_name="skill_run",
    )

    args = {"command": "rm -rf /"}
    result = asyncio.run(safe.run(None, args))

    assert result["success"] is False
    assert result["error"] == "SKILL_BLOCKED"
    assert runner.calls == 0

    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"


def test_skill_runner_allows_safe_command(tmp_path: Path):
    runner = _FakeSkillRunner()
    safe = SafetyReviewedSkillRunner(runner, PolicyConfig(), tool_name="skill_run")

    args = {"command": "echo hello"}
    result = asyncio.run(safe.run(None, args))

    assert result["success"] is True
    assert runner.calls == 1


def test_skill_runner_block_review_overrides_policy_records_intercepted(tmp_path: Path):
    """When block_review=True overrides policy.block_on_review=False, audit
    must record intercepted=True for a NEEDS_HUMAN_REVIEW hit.

    Regression for CongkeChen's review: intercepted=report.blocked used
    policy.block_on_review, so when the runner blocked review via the
    block_review parameter, the audit falsely showed intercepted=False.
    """
    runner = _FakeSkillRunner()
    # policy.block_on_review=False (default); block_review=True overrides it.
    safe = SafetyReviewedSkillRunner(
        runner,
        PolicyConfig(),
        audit_path=str(tmp_path / "audit.jsonl"),
        block_review=True,
        tool_name="skill_run",
    )

    # sleep 100 & triggers NEEDS_HUMAN_REVIEW (MEDIUM): background non-network process.
    args = {"command": "sleep 100 &"}
    result = asyncio.run(safe.run(None, args))

    assert result["success"] is False
    assert result["error"] == "SKILL_NEEDS_REVIEW"
    assert runner.calls == 0

    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "needs_human_review"
    assert rec["intercepted"] is True


def test_safety_wrapper_raise_on_deny_false_records_intercepted_false(tmp_path: Path):
    """When raise_on_deny=False, a DENY hit must not be recorded as intercepted.

    Regression for CongkeChen's review: intercepted=report.blocked used
    policy.block_on_review, so a DENY with raise_on_deny=False was falsely
    recorded as intercepted=True. The actual interception is raise_on_deny.
    """

    @safety_wrapper(
        tool_name="denied_tool",
        policy=PolicyConfig(),
        audit_path=str(tmp_path / "audit.jsonl"),
        raise_on_deny=False,
    )
    async def run_tool(*, script):
        return "ran"

    result = asyncio.run(run_tool(script="rm -rf /"))

    assert result == "ran"

    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is False
