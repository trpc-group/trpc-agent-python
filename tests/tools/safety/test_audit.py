"""Tests for trpc_agent_sdk.tools.safety._audit."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from trpc_agent_sdk.tools.safety._audit import (
    AuditSink,
    InMemoryAuditSink,
    JsonlAuditSink,
    NullAuditSink,
)
from trpc_agent_sdk.tools.safety._exceptions import SafetyAuditError
from trpc_agent_sdk.tools.safety._models import (
    RiskLevel,
    SafetyAuditEvent,
    SafetyDecision,
    ToolKind,
)


def _make_event() -> SafetyAuditEvent:
    return SafetyAuditEvent(
        event_id="e1",
        timestamp="2024-01-01T00:00:00Z",
        report_id="r1",
        tool_name="t",
        tool_kind=ToolKind.UNKNOWN,
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.HIGH,
        rule_ids=("FILE001_RECURSIVE_DELETE", ),
        duration_ms=0.5,
        redacted=False,
        execution_blocked=True,
        policy_hash="p",
        policy_version="1",
        script_sha256="s",
    )


class TestInMemoryAuditSink:

    def test_protocol(self):
        sink = InMemoryAuditSink()
        assert isinstance(sink, AuditSink)

    @pytest.mark.asyncio
    async def test_emit_and_read(self):
        sink = InMemoryAuditSink()
        await sink.emit(_make_event())
        events = sink.events
        assert len(events) == 1
        assert events[0].event_id == "e1"

    @pytest.mark.asyncio
    async def test_clear(self):
        sink = InMemoryAuditSink()
        await sink.emit(_make_event())
        sink.clear()
        assert sink.events == ()

    @pytest.mark.asyncio
    async def test_concurrent_emits_keep_order(self):
        sink = InMemoryAuditSink()
        events = [_make_event().model_copy(update={"event_id": f"e{i}"}) for i in range(5)]
        await asyncio.gather(*(sink.emit(e) for e in events))
        ids = [e.event_id for e in sink.events]
        assert sorted(ids) == ["e0", "e1", "e2", "e3", "e4"]


class TestJsonlAuditSink:

    @pytest.mark.asyncio
    async def test_append_writes_line(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink = JsonlAuditSink(path)
        await sink.emit(_make_event())
        await sink.emit(_make_event().model_copy(update={"event_id": "e2"}))
        content = path.read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.strip()]
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["event_id"] == "e1"

    @pytest.mark.asyncio
    async def test_oserror_raises_audit_error(self, tmp_path):
        # Pointing at a directory makes the open(..., "a") fail with
        # IsADirectoryError or PermissionError, both subclasses of OSError.
        sink = JsonlAuditSink(tmp_path)
        with pytest.raises(SafetyAuditError):
            await sink.emit(_make_event())

    def test_accepts_pathlike_and_str(self, tmp_path):
        JsonlAuditSink(tmp_path / "a.jsonl")
        JsonlAuditSink(str(tmp_path / "b.jsonl"))


class TestNullAuditSink:

    @pytest.mark.asyncio
    async def test_emit_noop(self):
        # Just verify it does not raise.
        await NullAuditSink().emit(_make_event())
