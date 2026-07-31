# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""JSONL concurrency, partial tail, sink isolation, and health signal tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from trpc_agent_sdk.safety import JsonlAuditSink
from trpc_agent_sdk.safety import SafetyDecision
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety._models import SafetyHealthSignal
from trpc_agent_sdk.safety._models import SafetyObservation
from trpc_agent_sdk.safety._monitor import MonitorSink

from .conftest import CANARY


class RecordingSink(MonitorSink):

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FailingSink(MonitorSink):

    def emit(self, event):
        del event
        raise OSError(CANARY)


def _observation() -> SafetyObservation:
    return SafetyObservation(
        decision="allow",
        blocked=False,
        review_required=False,
        source_type="test",
        language="python",
        script_hash="a" * 64,
        policy_version="v1",
        policy_hash="b" * 64,
        duration_ms=1.0,
    )


def test_jsonl_one_utf8_event_per_line_and_no_nan(tmp_path: Path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.emit(_observation())
    text = sink.path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert len(text.splitlines()) == 1
    payload = json.loads(text)
    assert payload["schema_version"] == "1"
    assert "NaN" not in text


def test_jsonl_threaded_writes_are_complete(tmp_path: Path):
    sink = JsonlAuditSink(tmp_path / "threaded.jsonl")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: sink.emit(_observation()), range(80)))
    assert len(sink.read_events()) == 80


def test_partial_tail_policy_is_explicit(tmp_path: Path):
    sink = JsonlAuditSink(tmp_path / "tail.jsonl")
    sink.emit(_observation())
    with sink.path.open("a", encoding="utf-8") as stream:
        stream.write('{"partial":')
    assert len(sink.read_events(ignore_partial_tail=True)) == 1


def test_allow_deny_review_all_produce_audit_events(tmp_path: Path, policy):
    sink = JsonlAuditSink(tmp_path / "decisions.jsonl")
    scanner = SafetyScanner(policy, audit_sink=sink)
    scanner.scan(SafetyScanRequest(script="print('ok')", language="python"))
    scanner.scan(SafetyScanRequest(script="import os\nos.remove('/etc/hosts')", language="python"))
    scanner.scan(SafetyScanRequest(script="import requests\nrequests.get(target)", language="python"))
    assert [item["decision"] for item in sink.read_events()] == ["allow", "deny", "needs_human_review"]


def test_audit_failure_does_not_change_decision_and_emits_safe_health(tmp_path: Path, policy):
    directory = tmp_path / "directory"
    directory.mkdir()
    recorder = RecordingSink()
    scanner = SafetyScanner(policy, audit_sink=JsonlAuditSink(directory), monitor_sinks=(recorder, ))
    report = scanner.scan(SafetyScanRequest(script="print('ok')", language="python"))
    assert report.decision is SafetyDecision.ALLOW
    health = [item for item in recorder.events if isinstance(item, SafetyHealthSignal)]
    assert health
    assert CANARY not in str(health)


def test_one_monitor_failure_does_not_stop_later_sinks(policy):
    recorder = RecordingSink()
    scanner = SafetyScanner(policy, monitor_sinks=(FailingSink(), recorder))
    report = scanner.scan(SafetyScanRequest(script="print('ok')", language="python"))
    assert report.decision is SafetyDecision.ALLOW
    assert any(isinstance(item, SafetyObservation) for item in recorder.events)
    assert any(isinstance(item, SafetyHealthSignal) for item in recorder.events)
    assert CANARY not in str(recorder.events)
