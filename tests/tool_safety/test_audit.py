"""Tests for the audit sinks."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety._audit import InMemoryAuditSink, JsonlAuditSink
from trpc_agent_sdk.tools.safety._models import (
    RiskLevel,
    SafetyAuditEvent,
    SafetyDecision,
    ToolKind,
)


def _event(decision: SafetyDecision = SafetyDecision.ALLOW) -> SafetyAuditEvent:
    return SafetyAuditEvent(
        event_id="e1",
        timestamp="2026-01-01T00:00:00Z",
        report_id="r1",
        tool_name="t",
        tool_kind=ToolKind.TOOL,
        decision=decision,
        risk_level=RiskLevel.INFO,
        rule_ids=("SAFE000",),
        duration_ms=1.0,
        redacted=False,
        execution_blocked=decision != SafetyDecision.ALLOW,
        policy_hash="p",
        policy_version="1",
        script_sha256="s",
    )


def test_in_memory_sink_collects_events():
    sink = InMemoryAuditSink()
    asyncio.run(sink.emit(_event()))
    asyncio.run(sink.emit(_event(SafetyDecision.DENY)))
    assert len(sink.events) == 2
    assert sink.events[0].decision == SafetyDecision.ALLOW
    assert sink.events[1].decision == SafetyDecision.DENY


def test_jsonl_sink_appends(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)
    asyncio.run(sink.emit(_event()))
    asyncio.run(sink.emit(_event(SafetyDecision.DENY)))
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2
    obj = json.loads(lines[0])
    assert obj["decision"] == "allow"
    assert obj["risk_level"] == "info"
    # Raw script/argv/env/cwd/args fields must not appear in audit events.
    for forbidden in ("script", "argv", "env", "cwd", "args"):
        assert forbidden not in obj, \
            f"forbidden key {forbidden!r} in audit event"
    assert "script_sha256" in obj


def test_jsonl_sink_redacts_no_raw_script(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)
    asyncio.run(sink.emit(_event()))
    payload = path.read_text(encoding="utf-8")
    for forbidden in ("script", "argv", "env", "cwd", "args"):
        for line in payload.splitlines():
            obj = json.loads(line)
            assert forbidden not in obj or forbidden == "script_sha256", \
                f"forbidden key {forbidden!r} in audit event"
