# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional OpenTelemetry attributes for tool safety checks."""

from __future__ import annotations

from .audit import risk_level
from .models import SafetyResult


def record_safety_attributes(result: SafetyResult) -> None:
    """Record tool safety attributes on the current OpenTelemetry span when available."""
    try:
        from opentelemetry import trace  # pylint: disable=import-outside-toplevel
    except Exception:  # pylint: disable=broad-except
        return

    try:
        span = trace.get_current_span()
        if span is None:
            return
        span.set_attribute("tool.safety.decision", result.decision.value)
        span.set_attribute("tool.safety.risk_level", risk_level(result.findings).value)
        span.set_attribute("tool.safety.rule_id", ",".join(finding.rule_id for finding in result.findings))
    except Exception:  # pylint: disable=broad-except
        return
