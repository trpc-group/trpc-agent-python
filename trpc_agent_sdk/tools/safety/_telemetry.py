# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OpenTelemetry integration for the Tool Script Safety Guard.

When the SDK's OpenTelemetry SDK is active, the guard sets the following
span attributes so that distributed traces carry safety decisions:

* ``tool.safety.decision``      — allow / deny / needs_human_review
* ``tool.safety.risk_level``    — none / low / medium / high / critical
* ``tool.safety.rule_ids``      — comma-separated list of triggered rule IDs
* ``tool.safety.scan_duration_ms``
* ``tool.safety.sanitized``     — whether evidence was redacted
* ``tool.safety.blocked``       — whether execution was intercepted
* ``tool.safety.script_type``   — python / bash / unknown
* ``tool.safety.tool_name``

If no span is active (no tracer or no current span), the calls are
silently no-ops — the guard never crashes because telemetry is missing.
"""

from __future__ import annotations

from typing import Optional

try:
    from opentelemetry import trace as _otel_trace
    _HAS_OTEL = True
except ImportError:  # pragma: no cover
    _HAS_OTEL = False
    _otel_trace = None  # type: ignore[assignment]

from ._models import AuditEvent
from ._models import SafetyReport


def _get_current_span():
    """Return the active OTel span, or ``None`` if OTel is unavailable."""
    if not _HAS_OTEL:
        return None
    try:
        return _otel_trace.get_current_span()
    except Exception:  # pylint: disable=broad-except
        return None


def report_to_span(report: SafetyReport, blocked: bool = False) -> None:
    """Set safety span attributes on the current OTel span.

    Args:
        report: The :class:`SafetyReport` produced by the scan.
        blocked: Whether execution was actually intercepted (denied/review).
    """
    span = _get_current_span()
    if span is None:
        return
    try:
        span.set_attribute("tool.safety.decision", report.decision.value)
        span.set_attribute("tool.safety.risk_level", report.risk_level.value)
        span.set_attribute("tool.safety.rule_ids", ",".join(
            f.rule_id for f in report.findings
        ))
        span.set_attribute("tool.safety.scan_duration_ms", round(report.scan_duration_ms, 3))
        span.set_attribute("tool.safety.sanitized", report.sanitized)
        span.set_attribute("tool.safety.blocked", blocked)
        span.set_attribute("tool.safety.script_type", report.script_type.value)
        span.set_attribute("tool.safety.tool_name", report.tool_name)
    except Exception:  # pylint: disable=broad-except
        # Telemetry must never break the safety guard.
        pass


def report_audit_to_span(event: AuditEvent) -> None:
    """Set audit-level span attributes from an :class:`AuditEvent`."""
    span = _get_current_span()
    if span is None:
        return
    try:
        span.set_attribute("tool.safety.decision", event.decision)
        span.set_attribute("tool.safety.risk_level", event.risk_level)
        span.set_attribute("tool.safety.rule_ids", ",".join(event.rule_ids))
        span.set_attribute("tool.safety.scan_duration_ms", round(event.scan_duration_ms, 3))
        span.set_attribute("tool.safety.sanitized", event.sanitized)
        span.set_attribute("tool.safety.blocked", event.blocked)
        span.set_attribute("tool.safety.script_type", event.script_type)
        span.set_attribute("tool.safety.tool_name", event.tool_name)
    except Exception:  # pylint: disable=broad-except
        pass
