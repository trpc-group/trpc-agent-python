# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the ToolSafetyGuardFilter pre-execution gate."""

from __future__ import annotations

import json
from pathlib import Path

from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools.safety import SafetyAuditLogger
from trpc_agent_sdk.tools.safety import ToolSafetyGuardFilter


class _Recorder:
    """A handle that records whether the wrapped tool was reached."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self):
        self.called = True
        return FilterResult(rsp={"success": True, "stdout": "ran"})


async def test_dangerous_command_blocked_before_execution() -> None:
    """A destructive command is blocked and the tool handle is never called."""
    guard = ToolSafetyGuardFilter()
    handle = _Recorder()

    result = await guard.run(None, {"command": "rm -rf /"}, handle)

    assert handle.called is False
    assert result.is_continue is False
    assert result.rsp["success"] is False
    assert result.rsp["safety_decision"] == "deny"
    assert "SAFETY_BLOCKED" in result.rsp["error"]


async def test_safe_command_passes_through() -> None:
    """A benign command runs: the handle is invoked and its result returned."""
    guard = ToolSafetyGuardFilter()
    handle = _Recorder()

    result = await guard.run(None, {"command": "ls -la"}, handle)

    assert handle.called is True
    assert result.rsp == {"success": True, "stdout": "ran"}


async def test_non_script_request_passes_through() -> None:
    """A request with no scriptable argument is not blocked."""
    guard = ToolSafetyGuardFilter()
    handle = _Recorder()

    result = await guard.run(None, {"foo": "bar"}, handle)

    assert handle.called is True


async def test_review_blocks_when_block_on_review_true() -> None:
    """With the fail-safe default, a review verdict also blocks."""
    guard = ToolSafetyGuardFilter(block_on_review=True)
    handle = _Recorder()

    result = await guard.run(None, {"command": "pip install requests"}, handle)

    assert handle.called is False
    assert result.is_continue is False
    assert result.rsp["safety_decision"] == "needs_human_review"


async def test_review_allowed_when_block_on_review_false() -> None:
    """When configured to only deny, a review verdict passes through."""
    guard = ToolSafetyGuardFilter(block_on_review=False)
    handle = _Recorder()

    result = await guard.run(None, {"command": "pip install requests"}, handle)

    assert handle.called is True


async def test_block_writes_audit_event(tmp_path: Path) -> None:
    """A blocking decision is persisted to the audit JSONL log."""
    audit_file = tmp_path / "audit.jsonl"
    guard = ToolSafetyGuardFilter(audit_logger=SafetyAuditLogger(audit_file))
    handle = _Recorder()

    await guard.run(None, {"command": "rm -rf /"}, handle)

    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["decision"] == "deny"
    assert event["blocked"] is True
    assert "FS001" in event["rule_ids"]
    assert "duration_ms" in event
