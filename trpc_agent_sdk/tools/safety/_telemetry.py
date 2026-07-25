# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OpenTelemetry integration for the Tool Script Safety Guard."""

from __future__ import annotations

from opentelemetry import trace

from ._types import SafetyReport


def set_safety_telemetry(report: SafetyReport) -> None:
    """Set all entries from report.telemetry_attributes on the current span.

    No-op when no span is active. None values are skipped.
    """
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return

    for key, value in report.telemetry_attributes.items():
        if value is not None:
            span.set_attribute(str(key), str(value))
