# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Audit logging and OpenTelemetry span tests."""
from __future__ import annotations

import json
from pathlib import Path

from examples.tool_safety.safety import AuditLogger
from examples.tool_safety.safety import PolicyConfig
from examples.tool_safety.safety import SafetyScanner
from examples.tool_safety.safety import ScanInput
from examples.tool_safety.safety.audit import _emit_telemetry


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
    """Issue: audit must contain tool name, decision, risk level, rule id,
    duration, sanitized flag, and intercepted flag."""
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
    # Must not raise.
    rec = audit.log(report)
    assert rec["decision"] == "allow"


def test_telemetry_does_not_raise_without_otel():
    scanner = SafetyScanner(PolicyConfig())
    report = scanner.scan(ScanInput(script="print('x')", language="python"))
    # No active span / no otel => must be a no-op.
    _emit_telemetry(report)


def test_telemetry_sets_span_attributes_when_recording():
    """When a recording span is active, tool.safety.* attributes are set."""
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
    except ImportError:
        import pytest
        pytest.skip("opentelemetry-sdk not installed")

    from opentelemetry import trace

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("test")

    scanner = SafetyScanner(PolicyConfig())
    inp = ScanInput(script="rm -rf /", language="bash")
    with tracer.start_as_current_span("safety-test"):
        report = scanner.scan(inp)
        _emit_telemetry(report)

    spans = exporter.get_finished_spans()
    assert spans
    attrs = spans[-1].attributes
    assert attrs.get("tool.safety.decision") == "deny"
    assert attrs.get("tool.safety.risk_level") in ("high", "critical")
    assert "tool.safety.rule_id" in attrs
