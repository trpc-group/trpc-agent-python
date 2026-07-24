# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import json
from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import RiskType
from trpc_agent_sdk.tools.safety import SafetyAuditLogger
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScanFinding
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import build_safety_audit_event
from trpc_agent_sdk.tools.safety import set_safety_span_attributes


def _report_with_duplicate_rules() -> SafetyReport:
    findings = [
        ScanFinding(
            rule_id="FILE_SENSITIVE_READ",
            risk_type=RiskType.FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            decision=SafetyDecision.DENY,
            message="Sensitive file read.",
            evidence="token=cleartext-value",
            recommendation="Remove the sensitive read.",
            redacted=True,
        ),
        ScanFinding(
            rule_id="NET_NON_WHITELIST_EGRESS",
            risk_type=RiskType.NETWORK_EGRESS,
            risk_level=RiskLevel.HIGH,
            decision=SafetyDecision.DENY,
            message="Network egress.",
            evidence="https://evil.example",
            recommendation="Use an allowlisted host.",
        ),
        ScanFinding(
            rule_id="FILE_SENSITIVE_READ",
            risk_type=RiskType.FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            decision=SafetyDecision.DENY,
            message="Duplicate sensitive file read.",
            evidence="password=cleartext-value",
            recommendation="Remove the sensitive read.",
            redacted=True,
        ),
    ]
    return SafetyReport(
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.CRITICAL,
        findings=findings,
        elapsed_ms=12.5,
        redacted=True,
        blocked=True,
        language=ScriptLanguage.PYTHON,
        scanner_version="0.1.0",
        policy_name="unit-policy",
        metadata={"target_tool": "workspace_exec"},
    )


class TestBuildSafetyAuditEvent:
    """Test audit-safe event creation from safety reports."""

    def test_event_maps_report_fields_and_deduplicates_rules(self):
        report = _report_with_duplicate_rules()

        event = build_safety_audit_event(
            report,
            cwd="workspace",
            function_call_id="fc-1",
            agent_name="agent",
        )

        assert event.tool_name == "workspace_exec"
        assert event.decision == SafetyDecision.DENY
        assert event.risk_level == RiskLevel.CRITICAL
        assert event.rule_ids == ["FILE_SENSITIVE_READ", "NET_NON_WHITELIST_EGRESS"]
        assert event.elapsed_ms == 12.5
        assert event.redacted is True
        assert event.blocked is True
        assert event.language == ScriptLanguage.PYTHON
        assert event.cwd == "workspace"
        assert event.function_call_id == "fc-1"
        assert event.agent_name == "agent"
        assert event.policy_name == "unit-policy"
        assert event.scanner_version == "0.1.0"
        assert event.finding_count == 3

    def test_explicit_tool_name_overrides_report_metadata(self):
        event = build_safety_audit_event(_report_with_duplicate_rules(), tool_name="manual_scan")

        assert event.tool_name == "manual_scan"

    def test_event_does_not_include_evidence_or_secret_values(self):
        event = build_safety_audit_event(_report_with_duplicate_rules())

        dumped = event.model_dump_json()

        assert "cleartext-value" not in dumped
        assert "https://evil.example" not in dumped
        assert "evidence" not in dumped


class TestSafetyAuditLogger:
    """Test optional JSONL audit writing."""

    def test_path_none_is_noop(self):
        logger = SafetyAuditLogger()

        logger.emit(build_safety_audit_event(_report_with_duplicate_rules()))

    def test_disabled_logger_does_not_write(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        logger = SafetyAuditLogger(audit_path, enabled=False)

        logger.emit(build_safety_audit_event(_report_with_duplicate_rules()))

        assert not audit_path.exists()

    def test_emit_appends_jsonl_events(self, tmp_path):
        audit_path = tmp_path / "nested" / "audit.jsonl"
        logger = SafetyAuditLogger(audit_path)

        logger.emit(build_safety_audit_event(_report_with_duplicate_rules(), tool_name="first"))
        logger.emit(build_safety_audit_event(_report_with_duplicate_rules(), tool_name="second"))

        lines = audit_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["tool_name"] == "first"
        assert second["tool_name"] == "second"
        assert first["rule_ids"] == ["FILE_SENSITIVE_READ", "NET_NON_WHITELIST_EGRESS"]
        assert "cleartext-value" not in audit_path.read_text(encoding="utf-8")


class TestSafetySpanAttributes:
    """Test OpenTelemetry span attribute helpers."""

    @patch("trpc_agent_sdk.tools.safety._audit.trace.get_current_span")
    def test_sets_aggregate_span_attributes(self, mock_get_current_span):
        span = MagicMock()
        mock_get_current_span.return_value = span

        set_safety_span_attributes(_report_with_duplicate_rules())

        span.set_attribute.assert_any_call("tool.safety.decision", "deny")
        span.set_attribute.assert_any_call("tool.safety.risk_level", "critical")
        span.set_attribute.assert_any_call(
            "tool.safety.rule_ids",
            "FILE_SENSITIVE_READ,NET_NON_WHITELIST_EGRESS",
        )
        span.set_attribute.assert_any_call("tool.safety.blocked", True)
        span.set_attribute.assert_any_call("tool.safety.redacted", True)
        span.set_attribute.assert_any_call("tool.safety.elapsed_ms", 12.5)
        span.set_attribute.assert_any_call("tool.safety.finding_count", 3)
        span.set_attribute.assert_any_call("tool.safety.policy_name", "unit-policy")
        span.set_attribute.assert_any_call("tool.safety.scanner_version", "0.1.0")
        span.set_attribute.assert_any_call("tool.safety.language", "python")
        span.set_attribute.assert_any_call("tool.safety.tool_name", "workspace_exec")

    @patch("trpc_agent_sdk.tools.safety._audit.trace.get_current_span")
    def test_span_attributes_do_not_include_evidence_or_secret_values(self, mock_get_current_span):
        span = MagicMock()
        mock_get_current_span.return_value = span

        set_safety_span_attributes(_report_with_duplicate_rules(), tool_name="manual_scan")

        calls = str(span.set_attribute.call_args_list)
        assert "cleartext-value" not in calls
        assert "https://evil.example" not in calls
        assert "evidence" not in calls
        span.set_attribute.assert_any_call("tool.safety.tool_name", "manual_scan")

    def test_unpatched_current_span_does_not_raise(self):
        set_safety_span_attributes(_report_with_duplicate_rules())
