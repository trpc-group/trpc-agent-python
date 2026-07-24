# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OpenTelemetry integration for the Tool Script Safety Guard.

Decorates existing tool execution spans with safety metadata.
"""

from __future__ import annotations

from opentelemetry import trace

from ._types import ScanReport


def set_safety_span_attrs(report: ScanReport):
    """Set safety-related span attributes on the current active span.

    If no span is active or the span is not recording, this is a no-op.
    Intended to be called from ToolSafetyFilter._before() so the attributes
    appear on the tool execution span created by ToolsProcessor._execute_tool().

    Attributes set:
        tool.safety.decision: allow | deny | needs_human_review
        tool.safety.risk_level: low | medium | high | critical | None
        tool.safety.rule_ids: JSON array of triggered rule IDs
        tool.safety.scan_duration_ms: float
    """
    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return

    span.set_attribute("tool.safety.decision", report.decision.value)
    if report.risk_level:
        span.set_attribute("tool.safety.risk_level", report.risk_level.value)
    span.set_attribute("tool.safety.rule_ids", [f.rule_id for f in report.findings])
    span.set_attribute("tool.safety.scan_duration_ms", report.scan_duration_ms)
