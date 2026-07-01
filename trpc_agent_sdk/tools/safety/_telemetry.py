# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OpenTelemetry integration for the Tool Script Safety Guard.

When the project has OpenTelemetry enabled the functions in this module
set span attributes that downstream observability tooling can consume.

Span attributes set:

===================== =============================================== ============
Attribute key          Description                                     Example value
===================== =============================================== ============
``tool.safety.decision``  Final decision                           ``"deny"``
``tool.safety.risk_level`` Highest risk level among findings       ``"critical"``
``tool.safety.rule_id``    Comma-separated list of triggered rules ``"FILE-001,NET-001"``
``tool.safety.tool_name``  Name of the scanned tool                ``"web_fetch"``
``tool.safety.scan_id``    UUID of this scan                       ``"abc123..."``
``tool.safety.duration_ms`` Scan wall-clock time in ms             ``12.34``
``tool.safety.script_lines`` Lines of code scanned                 ``120``
``tool.safety.execution_blocked`` Whether execution was stopped    ``true``
===================== =============================================== ============
"""

from __future__ import annotations

from typing import Optional

from trpc_agent_sdk.log import logger

from ._types import SafetyScanReport

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def set_safety_span_attributes(report: SafetyScanReport) -> None:
    """Set tool.safety.* attributes on the **current** OpenTelemetry span.

    If OpenTelemetry is not installed or no active span exists this is a
    silent no-op.

    Args:
        report: The completed ``SafetyScanReport``.
    """
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if not span or not span.is_recording():
            return
    except ImportError:
        return
    except Exception:  # pylint: disable=broad-except
        logger.debug("Could not access OpenTelemetry span.", exc_info=True)
        return

    _safe_set(span, "tool.safety.decision", report.decision.value)
    _safe_set(span, "tool.safety.risk_level", report.risk_level.value)
    _safe_set(span, "tool.safety.rule_id", ",".join(f.rule_id for f in report.findings) or "none")
    _safe_set(span, "tool.safety.tool_name", report.tool_name)
    _safe_set(span, "tool.safety.scan_id", report.scan_id)
    _safe_set(span, "tool.safety.duration_ms", report.scan_duration_ms)
    _safe_set(span, "tool.safety.script_lines", report.script_size_lines)
    _safe_set(span, "tool.safety.execution_blocked", str(report.execution_blocked).lower())


def _safe_set(span, key: str, value) -> None:
    try:
        span.set_attribute(key, value)
    except Exception:  # pylint: disable=broad-except
        logger.debug("Failed to set span attribute %s", key, exc_info=True)
