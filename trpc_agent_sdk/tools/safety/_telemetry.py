# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Telemetry helpers for tool script safety reports."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

from ._types import ToolSafetyReport


def apply_tool_safety_span_attributes(report: ToolSafetyReport) -> None:
    """Apply safety attributes to the current OpenTelemetry span.

    This helper is intentionally best-effort so the guard can be used without a
    configured exporter.
    """
    try:
        span = trace.get_current_span()
        for key, value in report.telemetry_attributes.items():
            span.set_attribute(key, _otel_value(value))
    except Exception:  # pylint: disable=broad-except
        return


def _otel_value(value: Any) -> Any:
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)
