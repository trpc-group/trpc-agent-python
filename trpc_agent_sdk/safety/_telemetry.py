# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional low-cardinality OpenTelemetry reporting for safety scans."""

from __future__ import annotations

from opentelemetry import metrics
from opentelemetry import trace

from ._models import SafetyObservation
from ._monitor import MonitorSink

_METER_SCOPE = "trpc.python.agent"
_meter = metrics.get_meter(_METER_SCOPE)
_scan_count = _meter.create_counter("safety.scan.count", unit="{scan}")
_blocked_count = _meter.create_counter("safety.blocked.count", unit="{scan}")
_scan_duration = _meter.create_histogram("safety.scan.duration", unit="ms")
_SOURCE_TYPES = {
    "callable",
    "cli",
    "code_executor",
    "evaluation",
    "mcp",
    "nested_script",
    "performance",
    "public_sample",
    "script",
    "tool",
    "workspace",
}
_LANGUAGES = {"argv", "python", "shell"}


def safety_attributes(observation: SafetyObservation) -> dict[str, str | bool | int | float]:
    """Build the complete bounded attribute set without source content."""
    return {
        "safety.decision": observation.decision.value,
        "safety.risk_level": observation.risk_level.value if observation.risk_level else "none",
        "safety.blocked": observation.blocked,
        "safety.review_required": observation.review_required,
        "safety.rule_count": len(observation.rule_ids),
        "safety.category_count": len(observation.categories),
        "safety.analysis_complete": observation.analysis_complete,
        "safety.failure_code": observation.failure_code or "none",
        "safety.source_type": observation.source_type if observation.source_type in _SOURCE_TYPES else "other",
        "safety.language": observation.language if observation.language in _LANGUAGES else "other",
        "safety.policy.version": observation.policy_version,
        "safety.duration_ms": observation.duration_ms,
    }


class OpenTelemetrySafetySink(MonitorSink):
    """Report a safety event; no provider or active span is a normal no-op."""

    def emit(self, event: SafetyObservation) -> None:
        if not isinstance(event, SafetyObservation):
            return
        attributes = safety_attributes(event)
        try:
            span = trace.get_current_span()
            if span is not None and span.is_recording():
                span.add_event("safety.scan", attributes=attributes)
            _scan_count.add(1, attributes)
            _scan_duration.record(event.duration_ms, attributes)
            if event.blocked:
                _blocked_count.add(1, attributes)
        except Exception:  # pylint: disable=broad-except
            raise RuntimeError("safety telemetry reporter failed") from None
