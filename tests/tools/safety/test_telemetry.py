"""Tests for trpc_agent_sdk.tools.safety._telemetry."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._models import (
    RiskLevel,
    SafetyDecision,
    SafetyReport,
    ToolKind,
)
from trpc_agent_sdk.tools.safety._telemetry import (
    TelemetrySink,
    build_audit_event,
    get_default_sink,
)


def _make_report(decision: SafetyDecision = SafetyDecision.ALLOW,
                 risk: RiskLevel = RiskLevel.INFO,
                 blocked: bool = False,
                 rule_ids: tuple[str, ...] = ("SAFE000", ),
                 duration_ms: float = 0.1) -> SafetyReport:
    return SafetyReport(
        report_id="r1",
        decision=decision,
        risk_level=risk,
        rule_ids=rule_ids,
        findings=(),
        recommendation="ok",
        policy_hash="p",
        policy_version="1",
        script_sha256="s",
        scan_duration_ms=duration_ms,
        redacted=False,
    )


class TestTelemetrySink:

    def test_construct_no_otel_installed(self):
        # Construction must not raise even when OTel is missing.
        sink = TelemetrySink()
        assert sink is not None

    def test_attributes_shape(self):
        sink = TelemetrySink()
        attrs = sink.attributes(_make_report(), tool_name="t", blocked=False)
        assert attrs["trpc_agent_sdk.tools.safety.decision"] == "allow"
        assert attrs["trpc_agent_sdk.tools.safety.risk_level"] == "info"
        assert attrs["trpc_agent_sdk.tools.safety.blocked"] is False
        assert attrs["trpc_agent_sdk.tools.safety.redacted"] is False
        assert attrs["trpc_agent_sdk.tools.safety.policy_hash"] == "p"
        assert attrs["trpc_agent_sdk.tools.safety.rule_id"] == "SAFE000"
        assert isinstance(attrs["trpc_agent_sdk.tools.safety.scan_duration_ms"], float)

    def test_attributes_truncates_rule_id_list(self):
        sink = TelemetrySink()
        rules = tuple(f"RULE{i:03d}" for i in range(20))
        attrs = sink.attributes(_make_report(rule_ids=rules), tool_name="t", blocked=False)
        # Bounded to first 8.
        assert attrs["trpc_agent_sdk.tools.safety.rule_id"].count(",") == 7

    def test_record_does_not_raise_without_otel(self):
        sink = TelemetrySink()
        # Should be a no-op rather than raising.
        sink.record(_make_report(), tool_name="t", blocked=False)
        sink.record(_make_report(decision=SafetyDecision.DENY, risk=RiskLevel.CRITICAL, blocked=True),
                    tool_name="t",
                    blocked=True)


def test_get_default_sink_singleton():
    a = get_default_sink()
    b = get_default_sink()
    assert a is b


class TestBuildAuditEvent:

    def test_construct(self):
        report = _make_report()
        event = build_audit_event(
            report=report,
            tool_name="t",
            tool_kind=ToolKind.UNKNOWN,
            execution_blocked=False,
            timestamp="2024-01-01T00:00:00Z",
        )
        assert event.event_id == report.report_id
        assert event.report_id == report.report_id
        assert event.tool_name == "t"
        assert event.execution_blocked is False
        assert event.timestamp == "2024-01-01T00:00:00Z"
        assert event.invocation_id is None

    def test_with_invocation_id(self):
        report = _make_report()
        event = build_audit_event(
            report=report,
            tool_name="t",
            tool_kind=ToolKind.UNKNOWN,
            execution_blocked=True,
            timestamp="ts",
            invocation_id="inv-1",
        )
        assert event.invocation_id == "inv-1"
