# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""OpenTelemetry attributes for tool safety decisions."""

from __future__ import annotations

from opentelemetry import trace

from ._models import SafetyReport


def trace_safety_report(report: SafetyReport, *, blocked: bool) -> None:
    """Attach stable safety attributes to the active span when tracing is enabled."""
    span = trace.get_current_span()
    span.set_attribute("tool.safety.decision", report.decision.value)
    span.set_attribute("tool.safety.risk_level", report.risk_level.value)
    span.set_attribute("tool.safety.rule_id", report.rule_ids[0] if report.rule_ids else "")
    span.set_attribute("tool.safety.rule_ids", ",".join(report.rule_ids))
    span.set_attribute("tool.safety.duration_ms", report.duration_ms)
    span.set_attribute("tool.safety.redacted", report.redacted)
    span.set_attribute("tool.safety.blocked", blocked)
    span.set_attribute("tool.safety.policy_version", report.policy_version)
