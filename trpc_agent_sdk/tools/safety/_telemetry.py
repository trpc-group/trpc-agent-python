# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional OpenTelemetry integration for safety scans."""

from __future__ import annotations

from ._types import SafetyReport


def record_safety_attributes(report: SafetyReport) -> None:
    """Attach safety attributes to the current OpenTelemetry span when available."""
    try:
        from opentelemetry import trace
    except Exception:  # pylint: disable=broad-except
        return

    try:
        span = trace.get_current_span()
        if span and span.is_recording():
            for key, value in report.telemetry_attributes.items():
                span.set_attribute(key, value)
    except Exception:  # pylint: disable=broad-except
        return
