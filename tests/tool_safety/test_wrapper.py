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


def _try_import_code_executors():
    try:
        from trpc_agent_sdk.code_executors import CodeExecutionInput
        from trpc_agent_sdk.code_executors import create_code_execution_result
        return CodeExecutionInput, create_code_execution_result
    except Exception:  # pylint: disable=broad-except
        return None, None


class _FakeInnerExecutor:
    def __init__(self, create_fn: Any) -> None:
        self._create_fn = create_fn
        self.calls: list[Any] = []

    async def execute_code(self, invocation_context, input_data):
        self.calls.append(input_data)
        return self._create_fn(stdout="ok")


def test_safe_code_executor_blocks_dangerous_python(tmp_path: Path):
    CodeExecutionInput, create_fn = _try_import_code_executors()
    if CodeExecutionInput is None:
        pytest.skip("trpc_agent_sdk.code_executors not importable")

    inner = _FakeInnerExecutor(create_fn)
    safe = SafeCodeExecutor(inner, _policy(tmp_path), audit_path=str(tmp_path / "audit.jsonl"))

    code = "import os\nos.system('rm -rf /')"
    inp = CodeExecutionInput(code=code)
    result = asyncio.run(safe.execute_code(None, inp))

    assert inner.calls == []
    assert "TOOL_SAFETY_DENY" in result.output
    assert result.outcome.name == "OUTCOME_FAILED"

    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True


def test_safe_code_executor_allows_safe_python(tmp_path: Path):
    CodeExecutionInput, create_fn = _try_import_code_executors()
    if CodeExecutionInput is None:
        pytest.skip("trpc_agent_sdk.code_executors not importable")

    inner = _FakeInnerExecutor(create_fn)
    safe = SafeCodeExecutor(inner, _policy(tmp_path))

    code = "print('hello world')"
    inp = CodeExecutionInput(code=code)
    result = asyncio.run(safe.execute_code(None, inp))

    assert len(inner.calls) == 1
    assert "ok" in result.output
    assert result.outcome.name == "OUTCOME_OK"


def test_safety_wrapper_blocks_dangerous_script(tmp_path: Path):
    policy = PolicyConfig(forbidden_paths=[".env"])

    @safety_wrapper(tool_name="deco_test", policy=policy,
                    audit_path=str(tmp_path / "audit.jsonl"))
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
        runner, PolicyConfig(), audit_path=str(tmp_path / "audit.jsonl"),
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
