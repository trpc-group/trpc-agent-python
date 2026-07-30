#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety._telemetry import record_safety_attributes


class RecordingSpan:
    def __init__(self):
        self.attributes = {}

    def is_recording(self):
        return True

    def set_attribute(self, key, value):
        self.attributes[key] = value


def _report():
    return SafetyReport(
        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
        risk_level=RiskLevel.MEDIUM,
        duration_ms=1.5,
        redacted=True,
        summary="review",
        policy_version="1",
        review_required=True,
    )


def test_active_span_receives_stable_safety_attributes(monkeypatch):
    span = RecordingSpan()
    monkeypatch.setattr("trpc_agent_sdk.tools.safety._telemetry.trace.get_current_span", lambda: span)

    record_safety_attributes(_report())

    assert span.attributes["tool.safety.decision"] == "needs_human_review"
    assert span.attributes["tool.safety.risk_level"] == "medium"
    assert span.attributes["tool.safety.execution_blocked"] is True
    assert span.attributes["tool.safety.redacted"] is True


def test_telemetry_failure_does_not_change_or_raise_from_decision(monkeypatch):
    class BrokenSpan(RecordingSpan):
        def set_attribute(self, key, value):
            del key, value
            raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr("trpc_agent_sdk.tools.safety._telemetry.trace.get_current_span", lambda: BrokenSpan())
    report = _report()

    record_safety_attributes(report)

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.blocked is True
