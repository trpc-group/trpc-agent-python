# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OpenTelemetry helpers for tool safety checks."""

from __future__ import annotations

from ._types import SafetyReport


def record_safety_attributes(report: SafetyReport) -> None:
    """Set tool.safety.* attributes on the current span when OpenTelemetry is available.

    Telemetry must never change the safety decision or tool execution result, so this helper
    intentionally behaves as a no-op when OpenTelemetry is unavailable or a span rejects attributes.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
    except Exception:
        return

    for key, value in report.telemetry_attributes.items():
        try:
            span.set_attribute(key, value)
        except Exception:
            continue
