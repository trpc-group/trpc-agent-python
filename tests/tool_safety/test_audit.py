# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Audit logging and OpenTelemetry span tests."""
from __future__ import annotations

import json
from pathlib import Path

from trpc_agent_sdk.safety import AuditLogger
from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ScanInput
from trpc_agent_sdk.safety._audit import _emit_telemetry


def test_audit_writes_jsonl(tmp_path: Path):
    audit = AuditLogger(tmp_path / "audit.jsonl")
    scanner = SafetyScanner(PolicyConfig())
    report = scanner.scan(ScanInput(script="rm -rf /", language="bash", tool_name="t"))
    rec = audit.log(report, script_path="x.sh", intercepted=True)

    assert rec["decision"] == "deny"
    assert rec["intercepted"] is True
    assert rec["tool_name"] == "t"
    assert rec["risk_level"] in ("high", "critical")

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines
    parsed = json.loads(lines[-1])
    for field in ("tool_name", "decision", "risk_level", "rule_ids",
                  "scan_duration_ms", "sanitized", "intercepted"):
        assert field in parsed


def test_audit_required_fields(tmp_path: Path):
    audit = AuditLogger(tmp_path / "a.jsonl")
    scanner = SafetyScanner(PolicyConfig())
    report = scanner.scan(ScanInput(script="import os\nos.system('x')", language="python"))
    rec = audit.log(report)
    for key in ("tool_name", "decision", "risk_level", "rule_ids",
                "scan_duration_ms", "sanitized", "intercepted"):
        assert key in rec, key


def test_audit_no_path_is_noop():
    audit = AuditLogger(None)
    scanner = SafetyScanner(PolicyConfig())
    report = scanner.scan(ScanInput(script="print('hi')", language="python"))
    rec = audit.log(report)
    assert rec["decision"] == "allow"


def test_telemetry_does_not_raise_without_otel():
    scanner = SafetyScanner(PolicyConfig())
    report = scanner.scan(ScanInput(script="print('x')", language="python"))
    _emit_telemetry(report)


def test_telemetry_sets_span_attributes_when_recording():
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
    except ImportError:
        import pytest
        pytest.skip("opentelemetry-sdk not installed")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("safety-test")

    scanner = SafetyScanner(PolicyConfig())
    inp = ScanInput(script="rm -rf /", language="bash")
    with tracer.start_as_current_span("safety-test"):
        report = scanner.scan(inp)
        _emit_telemetry(report)

    spans = exporter.get_finished_spans()
    assert spans, "no spans exported — provider not wired"
    attrs = spans[-1].attributes
    assert attrs.get("tool.safety.decision") == "deny"
    assert attrs.get("tool.safety.risk_level") in ("high", "critical")
    assert "tool.safety.rule_id" in attrs
