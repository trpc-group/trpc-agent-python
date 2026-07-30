# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the SafeCodeExecutor wrapper."""

from __future__ import annotations

import json
from pathlib import Path

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.code_executors._types import CodeBlock

from trpc_agent_sdk.tools.safety import SafeCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyAuditLogger


class _FakeInnerExecutor(BaseCodeExecutor):
    """Inner executor that records whether it actually ran."""

    ran: bool = False

    async def execute_code(self, invocation_context, code_execution_input) -> CodeExecutionResult:
        # Use object.__setattr__ so a pydantic model can track the call.
        object.__setattr__(self, "ran", True)
        return create_code_execution_result(stdout="inner ran")


def _blocks(*blocks: tuple[str, str]) -> CodeExecutionInput:
    """Build a CodeExecutionInput from (language, code) tuples."""
    return CodeExecutionInput(code_blocks=[CodeBlock(language=lang, code=code) for lang, code in blocks])


async def test_dangerous_block_blocks_inner_executor() -> None:
    """A dangerous code block prevents the inner executor from running."""
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner=inner)

    result = await safe.execute_code(None, _blocks(("bash", "rm -rf /")))

    assert inner.ran is False
    assert "SAFETY_BLOCKED" in result.output


async def test_safe_block_delegates_to_inner() -> None:
    """A benign block is forwarded to the inner executor."""
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner=inner)

    result = await safe.execute_code(None, _blocks(("python", "print('hi')")))

    assert inner.ran is True
    assert "inner ran" in result.output


async def test_first_dangerous_block_short_circuits() -> None:
    """When any block is dangerous the whole batch is refused."""
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner=inner)

    result = await safe.execute_code(
        None, _blocks(("python", "print('ok')"), ("bash", "cat ~/.ssh/id_rsa")))

    assert inner.ran is False
    assert "SAFETY_BLOCKED" in result.output


async def test_raw_code_field_scanned() -> None:
    """When only the ``code`` field is set, it is still scanned."""
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner=inner)

    result = await safe.execute_code(None, CodeExecutionInput(code="rm -rf /"))

    assert inner.ran is False
    assert "SAFETY_BLOCKED" in result.output


async def test_blocked_block_writes_audit(tmp_path: Path) -> None:
    """A blocked code block is recorded in the audit log."""
    audit_file = tmp_path / "audit.jsonl"
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner=inner, audit_logger=SafetyAuditLogger(audit_file))

    await safe.execute_code(None, _blocks(("bash", "rm -rf /")))

    assert audit_file.exists()
    event = json.loads(audit_file.read_text(encoding="utf-8").strip().splitlines()[0])
    assert event["decision"] == "deny"
    assert event["blocked"] is True


async def test_review_block_respects_block_on_review_flag() -> None:
    """A review verdict blocks only when ``block_on_review`` is True."""
    blocking = SafeCodeExecutor(inner=_FakeInnerExecutor(), block_on_review=True)
    result = await blocking.execute_code(None, _blocks(("bash", "pip install requests")))
    assert blocking.inner.ran is False
    assert "SAFETY_BLOCKED" in result.output

    passing_inner = _FakeInnerExecutor()
    passing = SafeCodeExecutor(inner=passing_inner, block_on_review=False)
    await passing.execute_code(None, _blocks(("bash", "pip install requests")))
    assert passing_inner.ran is True


async def test_raw_safe_code_field_delegates_to_inner() -> None:
    """A benign raw ``code`` field is scanned and then forwarded."""
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner=inner)
    result = await safe.execute_code(None, CodeExecutionInput(code="print('hi')"))
    assert inner.ran is True
    assert "inner ran" in result.output


async def test_empty_code_block_is_skipped() -> None:
    """A blank code block is skipped and the remaining benign block runs."""
    inner = _FakeInnerExecutor()
    safe = SafeCodeExecutor(inner=inner)
    await safe.execute_code(None, _blocks(("python", "   "), ("python", "print('hi')")))
    assert inner.ran is True


def test_audit_logger_path_property(tmp_path: Path) -> None:
    """The ``path`` property reflects the configured destination (or None)."""
    assert SafetyAuditLogger().path is None
    target = tmp_path / "audit.jsonl"
    assert SafetyAuditLogger(target).path == target
