# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for tool safety telemetry helpers."""

from __future__ import annotations

import sys
import builtins
from types import SimpleNamespace

from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import record_safety_attributes


class _RecordingSpan:

    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _RejectingSpan:

    def set_attribute(self, key, value):
        raise RuntimeError("span closed")


def test_record_safety_attributes_sets_current_span_attributes(monkeypatch):
    span = _RecordingSpan()
    trace = SimpleNamespace(get_current_span=lambda: span)
    monkeypatch.setitem(sys.modules, "opentelemetry", SimpleNamespace(trace=trace))
    report = ToolScriptSafetyScanner().scan_script("print('ok')", "python", tool_name="python")

    record_safety_attributes(report)

    assert span.attributes["tool.safety.decision"] == "allow"
    assert span.attributes["tool.safety.tool_name"] == "python"


def test_record_safety_attributes_ignores_span_attribute_errors(monkeypatch):
    trace = SimpleNamespace(get_current_span=lambda: _RejectingSpan())
    monkeypatch.setitem(sys.modules, "opentelemetry", SimpleNamespace(trace=trace))
    report = ToolScriptSafetyScanner().scan_script("rm -rf /", "bash")

    record_safety_attributes(report)


def test_record_safety_attributes_is_noop_when_otel_import_fails(monkeypatch):
    real_import = builtins.__import__

    def reject_opentelemetry(name, *args, **kwargs):
        if name == "opentelemetry":
            raise ImportError("missing opentelemetry")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_opentelemetry)
    report = ToolScriptSafetyScanner().scan_script("print('ok')", "python")

    record_safety_attributes(report)
