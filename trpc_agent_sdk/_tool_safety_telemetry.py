# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Telemetry helpers for tool safety review results."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk._tool_safety import SafetyReview

try:
    from opentelemetry import trace
except Exception:  # pylint: disable=broad-except
    trace = None  # type: ignore[assignment]


def trace_tool_safety_review(review: SafetyReview) -> None:
    """Write tool safety review attributes to the current span.

    This helper is intentionally best-effort: safety decisions must not depend
    on telemetry availability or exporter configuration.
    """
    try:
        if trace is None:
            return
        span = trace.get_current_span()
        span.set_attribute("tool.safety.decision", _attribute_value(review.decision))
        span.set_attribute("tool.safety.risk_level", _attribute_value(review.report.get("risk_level", "")))
        span.set_attribute("tool.safety.rule_id", _attribute_value(review.rule_id))
    except Exception:  # pylint: disable=broad-except
        return


def _attribute_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(item) for item in value)
    return str(value)
