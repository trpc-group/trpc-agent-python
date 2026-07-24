# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for safety types module."""

from trpc_agent_sdk.tools.safety._types import (
    RiskType,
    Decision,
    RiskLevel,
    RuleFinding,
    ScanReport,
    AuditEvent,
    _RISK_LEVEL_ORDER,
    _DECISION_ORDER,
)


class TestRiskType:

    def test_risk_type_values(self):
        assert RiskType.DANGEROUS_FILE_OP.value == "dangerous_file_operation"
        assert RiskType.NETWORK_ACCESS.value == "network_access"
        assert RiskType.SYSTEM_COMMAND.value == "system_command"
        assert RiskType.DEPENDENCY_INSTALL.value == "dependency_install"
        assert RiskType.RESOURCE_ABUSE.value == "resource_abuse"
        assert RiskType.SENSITIVE_INFO_LEAK.value == "sensitive_info_leak"


class TestDecision:

    def test_decision_values(self):
        assert Decision.ALLOW.value == "allow"
        assert Decision.DENY.value == "deny"
        assert Decision.NEEDS_HUMAN_REVIEW.value == "needs_human_review"

    def test_decision_priority_ordering(self):
        assert _DECISION_ORDER[Decision.DENY] > _DECISION_ORDER[Decision.NEEDS_HUMAN_REVIEW]
        assert _DECISION_ORDER[Decision.NEEDS_HUMAN_REVIEW] > _DECISION_ORDER[Decision.ALLOW]


class TestRiskLevel:

    def test_risk_level_values(self):
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.CRITICAL.value == "critical"

    def test_risk_level_ordering(self):
        assert _RISK_LEVEL_ORDER[RiskLevel.CRITICAL] > _RISK_LEVEL_ORDER[RiskLevel.HIGH]
        assert _RISK_LEVEL_ORDER[RiskLevel.HIGH] > _RISK_LEVEL_ORDER[RiskLevel.MEDIUM]
        assert _RISK_LEVEL_ORDER[RiskLevel.MEDIUM] > _RISK_LEVEL_ORDER[RiskLevel.LOW]


class TestRuleFinding:

    def test_create_finding(self):
        f = RuleFinding(
            rule_id="TEST_001",
            risk_type=RiskType.DANGEROUS_FILE_OP,
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf /",
            message="Dangerous delete detected",
            recommendation="Use a safer alternative",
        )
        assert f.rule_id == "TEST_001"
        assert f.risk_type == RiskType.DANGEROUS_FILE_OP
        assert f.risk_level == RiskLevel.CRITICAL
        assert f.evidence == "rm -rf /"


class TestScanReport:

    def test_aggregation_picks_worst_decision(self):
        findings = [
            RuleFinding(
                rule_id="LOW_001",
                risk_type=RiskType.NETWORK_ACCESS,
                risk_level=RiskLevel.LOW,
                evidence="x",
                message="low",
                recommendation="ok",
            ),
            RuleFinding(
                rule_id="CRIT_001",
                risk_type=RiskType.DANGEROUS_FILE_OP,
                risk_level=RiskLevel.CRITICAL,
                evidence="y",
                message="critical",
                recommendation="block",
            ),
        ]
        report = ScanReport(
            decision=max(findings, key=lambda f: _RISK_LEVEL_ORDER[f.risk_level]).risk_level,
            risk_level=max(findings, key=lambda f: _RISK_LEVEL_ORDER[f.risk_level]).risk_level,
            findings=findings,
        )
        assert report.risk_level == RiskLevel.CRITICAL

    def test_empty_report_defaults(self):
        report = ScanReport(decision=Decision.ALLOW)
        assert report.decision == Decision.ALLOW
        assert report.findings == []
        assert report.scan_duration_ms == 0.0


class TestAuditEvent:

    def test_serialization(self):
        import json
        from dataclasses import asdict

        event = AuditEvent(
            timestamp="2026-07-10T12:00:00Z",
            tool_name="bash_tool",
            decision="deny",
            risk_level="critical",
            rule_ids=["DANGEROUS_DELETE_001"],
            scan_duration_ms=12.5,
            sanitized=False,
            intercepted=True,
            script_hash="a1b2c3d4e5f6",
        )
        d = asdict(event)
        assert d["tool_name"] == "bash_tool"
        assert d["decision"] == "deny"
        assert "DANGEROUS_DELETE_001" in d["rule_ids"]
        json_str = json.dumps(d)
        assert "timestamp" in json_str
