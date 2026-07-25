# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the safety telemetry helper."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import set_safety_telemetry


class TestSetSafetyTelemetry:

    def test_noop_when_no_active_span(self):
        report = SafetyReport(
            tool_name="test",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=0,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        set_safety_telemetry(report)  # should not raise

    @patch("trpc_agent_sdk.tools.safety._telemetry.trace")
    def test_sets_attributes_from_report(self, mock_trace):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_trace.get_current_span.return_value = mock_span

        report = SafetyReport(
            tool_name="test_tool",
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            blocked=True,
            sanitized=False,
            duration_ms=15,
            language=ScriptLanguage.BASH,
            target=ScanTarget.TOOL,
            telemetry_attributes={
                "tool.safety.decision": "deny",
                "tool.safety.risk_level": "high",
                "tool.safety.rule_id": "R001,R006",
                "tool.safety.target": "tool",
                "tool.safety.language": "bash",
            },
        )
        set_safety_telemetry(report)

        mock_span.set_attribute.assert_any_call("tool.safety.decision", "deny")
        mock_span.set_attribute.assert_any_call("tool.safety.risk_level", "high")
        mock_span.set_attribute.assert_any_call("tool.safety.rule_id", "R001,R006")
        mock_span.set_attribute.assert_any_call("tool.safety.target", "tool")
        mock_span.set_attribute.assert_any_call("tool.safety.language", "bash")

    @patch("trpc_agent_sdk.tools.safety._telemetry.trace")
    def test_skips_none_values(self, mock_trace):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_trace.get_current_span.return_value = mock_span

        report = SafetyReport(
            tool_name="test",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=0,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
            telemetry_attributes={
                "tool.safety.decision": "allow",
                "tool.safety.none_field": None,
            },
        )
        set_safety_telemetry(report)
        mock_span.set_attribute.assert_called_once_with("tool.safety.decision", "allow")

    @patch("trpc_agent_sdk.tools.safety._telemetry.trace")
    def test_empty_attributes_noop(self, mock_trace):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_trace.get_current_span.return_value = mock_span

        report = SafetyReport(
            tool_name="test",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=0,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        set_safety_telemetry(report)
        mock_span.set_attribute.assert_not_called()

    @patch("trpc_agent_sdk.tools.safety._telemetry.trace")
    def test_noop_when_span_not_recording(self, mock_trace):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = False
        mock_trace.get_current_span.return_value = mock_span

        report = SafetyReport(
            tool_name="test",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=0,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
            telemetry_attributes={"key": "value"},
        )
        set_safety_telemetry(report)
        mock_span.set_attribute.assert_not_called()
