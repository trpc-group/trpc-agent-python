# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for safety telemetry integration."""

from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.tools.safety._telemetry import set_safety_span_attrs
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import RiskType
from trpc_agent_sdk.tools.safety._types import RuleFinding
from trpc_agent_sdk.tools.safety._types import ScanReport


class TestSetSafetySpanAttrs:
    def test_span_attributes_set_on_active_span(self):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        report = ScanReport(
            decision=Decision.DENY,
            risk_level=RiskLevel.CRITICAL,
            findings=[
                RuleFinding(
                    rule_id="DANGEROUS_DELETE_001",
                    risk_type=RiskType.DANGEROUS_FILE_OP,
                    risk_level=RiskLevel.CRITICAL,
                    evidence="rm -rf /",
                    message="dangerous delete",
                    recommendation="don't",
                ),
            ],
            scan_duration_ms=15.0,
        )

        with patch("trpc_agent_sdk.tools.safety._telemetry.trace.get_current_span", return_value=mock_span):
            set_safety_span_attrs(report)

        mock_span.set_attribute.assert_any_call("tool.safety.decision", "deny")
        mock_span.set_attribute.assert_any_call("tool.safety.risk_level", "critical")
        mock_span.set_attribute.assert_any_call("tool.safety.scan_duration_ms", 15.0)

    def test_no_crash_when_no_span(self):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = False

        report = ScanReport(decision=Decision.ALLOW)

        with patch("trpc_agent_sdk.tools.safety._telemetry.trace.get_current_span", return_value=mock_span):
            set_safety_span_attrs(report)

        mock_span.set_attribute.assert_not_called()
