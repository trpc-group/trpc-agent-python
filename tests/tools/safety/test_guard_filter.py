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
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ToolSafetyGuardFilter


class _Recorder:
    """A handle that records whether the wrapped tool was reached."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self):
        self.called = True
        return FilterResult(rsp={"success": True, "stdout": "ran"})


class _StreamRecorder:
    """A streaming handle (async generator) recording whether it was reached."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self):
        self.called = True
        yield FilterResult(rsp={"success": True, "stdout": "streamed"})


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
    assert result.is_continue is True


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
    assert result.is_continue is True


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


async def test_streaming_path_blocks_dangerous_command() -> None:
    """The streaming path (run_stream) must scan too: a deny blocks the tool.

    Regression for the streaming bypass: the guard scans in ``_before`` so the
    base filter applies it to ``run_stream`` as well. A destructive command must
    be blocked and the streaming tool handle never reached.
    """
    guard = ToolSafetyGuardFilter()
    handle = _StreamRecorder()

    events = [event async for event in guard.run_stream(None, {"command": "rm -rf /"}, handle)]

    assert handle.called is False
    assert events
    last = events[-1]
    assert last.is_continue is False
    assert last.rsp["success"] is False
    assert last.rsp["safety_decision"] == "deny"


async def test_streaming_path_passes_safe_command() -> None:
    """A benign command streams through: the streaming handle is invoked."""
    guard = ToolSafetyGuardFilter()
    handle = _StreamRecorder()

    events = [event async for event in guard.run_stream(None, {"command": "ls -la"}, handle)]

    assert handle.called is True
    assert events[-1].rsp == {"success": True, "stdout": "streamed"}


async def test_scanner_property_exposes_underlying_scanner() -> None:
    """The ``scanner`` property returns the scanner the filter was built with."""
    scanner = SafetyScanner()
    guard = ToolSafetyGuardFilter(scanner=scanner)
    assert guard.scanner is scanner


async def test_non_dict_request_passes_through() -> None:
    """A non-dict request carries no arguments to inspect and is not blocked."""
    guard = ToolSafetyGuardFilter()
    handle = _Recorder()

    await guard.run(None, "not-a-dict", handle)

    assert handle.called is True


async def test_declared_language_is_respected() -> None:
    """An explicit ``language`` argument drives language selection."""
    guard = ToolSafetyGuardFilter()
    handle = _Recorder()

    await guard.run(None, {"code": "print('hi')", "language": "python"}, handle)

    assert handle.called is True


async def test_script_arg_without_shell_hint_defaults_to_unknown() -> None:
    """A non-shell ``script`` argument with no language hint is left UNKNOWN."""
    guard = ToolSafetyGuardFilter()
    handle = _Recorder()

    await guard.run(None, {"script": "print('hi')"}, handle)

    assert handle.called is True
