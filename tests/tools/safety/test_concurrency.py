# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Single-process concurrency safety tests."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety._audit import create_audit_event
from trpc_agent_sdk.tools.safety._audit import emit_report

EVENT_COUNT = 50
WORKER_COUNT = 8


def _event(index):
    report = SafetyReport(
        decision=SafetyDecision.ALLOW,
        risk_level=RiskLevel.NONE,
        duration_ms=float(index),
        redacted=False,
        summary="safe",
        max_output_bytes=100,
    )
    return create_audit_event(report, f"tool-{index}", False)


def test_concurrent_jsonl_writes_do_not_interleave(tmp_path):
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)

    with ThreadPoolExecutor(max_workers=WORKER_COUNT) as pool:
        list(pool.map(lambda index: sink.emit(_event(index)), range(EVENT_COUNT)))

    lines = path.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == EVENT_COUNT
    assert {item["tool_name"] for item in parsed} == {f"tool-{index}" for index in range(EVENT_COUNT)}


def test_multiple_sinks_for_same_path_share_lock(tmp_path):
    path = tmp_path / "audit.jsonl"
    sinks = [JsonlAuditSink(path), JsonlAuditSink(path)]

    def emit(index):
        sinks[index % len(sinks)].emit(_event(index))

    with ThreadPoolExecutor(max_workers=WORKER_COUNT) as pool:
        list(pool.map(emit, range(EVENT_COUNT)))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len([json.loads(line) for line in lines]) == EVENT_COUNT


def test_emit_report_serializes_custom_sink_calls():

    class OverlapDetectingSink:

        def __init__(self):
            self.active = 0
            self.overlapped = False
            self.lock = threading.Lock()

        def emit(self, event):
            del event
            with self.lock:
                self.active += 1
                self.overlapped = self.overlapped or self.active > 1
            time.sleep(0.001)
            with self.lock:
                self.active -= 1

    sink = OverlapDetectingSink()
    report = SafetyReport(
        decision=SafetyDecision.ALLOW,
        risk_level=RiskLevel.NONE,
        duration_ms=1,
        redacted=False,
        summary="safe",
        max_output_bytes=100,
    )
    with ThreadPoolExecutor(max_workers=WORKER_COUNT) as pool:
        list(pool.map(lambda _: emit_report(sink, report, "tool"), range(EVENT_COUNT)))
    assert sink.overlapped is False


def test_independent_audit_sinks_do_not_share_one_lock():

    class BarrierSink:

        def __init__(self, barrier):
            self.barrier = barrier

        def emit(self, event):
            del event
            self.barrier.wait(timeout=1)

    report = SafetyReport(
        decision=SafetyDecision.ALLOW,
        risk_level=RiskLevel.NONE,
        duration_ms=1,
        redacted=False,
        summary="safe",
        max_output_bytes=100,
    )
    barrier = threading.Barrier(2)
    sinks = [BarrierSink(barrier), BarrierSink(barrier)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda sink: emit_report(sink, report, "tool"), sinks))
