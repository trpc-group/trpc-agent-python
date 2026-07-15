"""OpenTelemetry integration that no-ops cleanly when OTel is absent.

We record a small, low-cardinality set of span attributes plus a counter
and histogram. Evidence, script hashes, and env values are never emitted
as span attributes: they would inflate cardinality and risk leaking
secrets via tracing backends.
"""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.tools.safety._models import SafetyAuditEvent, SafetyReport


_SPAN_ATTRS = (
    "trpc_agent_sdk.tools.safety.decision",
    "trpc_agent_sdk.tools.safety.risk_level",
    "trpc_agent_sdk.tools.safety.rule_id",
    "trpc_agent_sdk.tools.safety.blocked",
    "trpc_agent_sdk.tools.safety.redacted",
    "trpc_agent_sdk.tools.safety.scan_duration_ms",
    "trpc_agent_sdk.tools.safety.policy_hash",
)

_METRIC_SCAN_COUNT = "trpc_agent.tool_safety.scan_count"
_METRIC_BLOCK_COUNT = "trpc_agent.tool_safety.block_count"
_METRIC_SCAN_DURATION = "trpc_agent.tool_safety.scan_duration_ms"


class TelemetrySink:
    """Best-effort telemetry facade.

    The constructor probes for an OpenTelemetry tracer/meter provider. If
    none is configured, all methods become no-ops. This keeps the guard
    safe to import in environments that do not run OTel.
    """

    def __init__(self) -> None:
        self._tracer = _safe_tracer()
        self._meter = _safe_meter()
        self._counter = _safe_counter(self._meter, _METRIC_SCAN_COUNT)
        self._block_counter = _safe_counter(self._meter, _METRIC_BLOCK_COUNT)
        self._histogram = _safe_histogram(self._meter, _METRIC_SCAN_DURATION)

    def record(self, report: SafetyReport, *,
               tool_name: str, blocked: bool) -> None:
        attrs = self.attributes(report, tool_name=tool_name, blocked=blocked)
        self._emit_span_attrs(attrs)
        if self._counter is not None:
            try:
                self._counter.add(
                    1, {
                        "decision": report.decision.value,
                        "risk_level": report.risk_level.label(),
                        "tool_name": tool_name,
                    }
                )
            except Exception:  # pragma: no cover - defensive
                pass
        if blocked and self._block_counter is not None:
            try:
                primary = report.rule_ids[0] if report.rule_ids else ""
                self._block_counter.add(
                    1, {
                        "decision": report.decision.value,
                        "rule_id": primary,
                        "tool_name": tool_name,
                    }
                )
            except Exception:  # pragma: no cover - defensive
                pass
        if self._histogram is not None:
            try:
                self._histogram.record(
                    report.scan_duration_ms,
                    {"decision": report.decision.value,
                     "tool_name": tool_name},
                )
            except Exception:  # pragma: no cover - defensive
                pass

    @staticmethod
    def attributes(report: SafetyReport, *,
                   tool_name: str, blocked: bool) -> dict[str, Any]:
        """Return the span attribute dict for a report.

        ``rule_id`` is joined into a bounded comma-separated string (the
        list is naturally bounded by the rule catalog size).
        """

        rule_ids = ",".join(report.rule_ids[:8])
        return {
            "trpc_agent_sdk.tools.safety.decision": report.decision.value,
            "trpc_agent_sdk.tools.safety.risk_level": report.risk_level.label(),
            "trpc_agent_sdk.tools.safety.rule_id": rule_ids,
            "trpc_agent_sdk.tools.safety.blocked": bool(blocked),
            "trpc_agent_sdk.tools.safety.redacted": bool(report.redacted),
            "trpc_agent_sdk.tools.safety.scan_duration_ms": float(report.scan_duration_ms),
            "trpc_agent_sdk.tools.safety.policy_hash": report.policy_hash,
        }

    def _emit_span_attrs(self, attrs: dict[str, Any]) -> None:
        if self._tracer is None:
            return
        try:
            from opentelemetry.trace import (  # type: ignore
                INVALID_SPAN, get_current_span,
            )
            span = get_current_span()
            if span is INVALID_SPAN or span is None:
                return
            # ``set_attributes`` exists on Span starting from OTel 1.x.
            set_attributes = getattr(span, "set_attributes", None)
            if set_attributes is not None:
                set_attributes(attrs)
                return
            for key, value in attrs.items():
                span.set_attribute(key, value)
        except Exception:  # pragma: no cover - defensive
            pass


def _safe_tracer():
    try:
        from opentelemetry.trace import get_tracer  # type: ignore
        return get_tracer("trpc_agent_sdk.tools.safety")
    except Exception:
        return None


def _safe_meter():
    try:
        from opentelemetry.metrics import get_meter  # type: ignore
        return get_meter("trpc_agent_sdk.tools.safety")
    except Exception:
        return None


def _safe_counter(meter, name: str):
    if meter is None:
        return None
    try:
        return meter.create_counter(name)
    except Exception:
        return None


def _safe_histogram(meter, name: str):
    if meter is None:
        return None
    try:
        return meter.create_histogram(name)
    except Exception:
        return None


_default_sink: TelemetrySink | None = None


def get_default_sink() -> TelemetrySink:
    global _default_sink
    if _default_sink is None:
        _default_sink = TelemetrySink()
    return _default_sink


def build_audit_event(
    *,
    report,
    tool_name: str,
    tool_kind,
    execution_blocked: bool,
    timestamp: str,
    invocation_id: str | None = None,
):
    """Build a :class:`SafetyAuditEvent` from a report."""

    from trpc_agent_sdk.tools.safety._models import SafetyAuditEvent

    return SafetyAuditEvent(
        event_id=report.report_id,
        timestamp=timestamp,
        report_id=report.report_id,
        tool_name=tool_name,
        tool_kind=tool_kind,
        decision=report.decision,
        risk_level=report.risk_level,
        rule_ids=report.rule_ids,
        duration_ms=report.scan_duration_ms,
        redacted=report.redacted,
        execution_blocked=execution_blocked,
        policy_hash=report.policy_hash,
        policy_version=report.policy_version,
        script_sha256=report.script_sha256,
        invocation_id=invocation_id,
    )
