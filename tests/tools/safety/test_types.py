# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for safety guard enums, data models, and helpers."""

from __future__ import annotations

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import RiskType
from trpc_agent_sdk.tools.safety import SafetyFinding
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScanRequest
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import aggregate_decision
from trpc_agent_sdk.tools.safety import decision_order
from trpc_agent_sdk.tools.safety import max_risk_level
from trpc_agent_sdk.tools.safety import normalize_language
from trpc_agent_sdk.tools.safety import risk_order


class TestDecision:

    def test_values(self):
        assert Decision.ALLOW == "allow"
        assert Decision.DENY == "deny"
        assert Decision.NEEDS_HUMAN_REVIEW == "needs_human_review"

    def test_is_str(self):
        assert isinstance(Decision.ALLOW, str)


class TestRiskLevel:

    def test_values(self):
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.CRITICAL == "critical"


class TestScriptLanguage:

    def test_values(self):
        assert ScriptLanguage.PYTHON == "python"
        assert ScriptLanguage.BASH == "bash"


class TestNormalizeLanguage:

    def test_python_variants(self):
        assert normalize_language("py") == ScriptLanguage.PYTHON
        assert normalize_language("python") == ScriptLanguage.PYTHON
        assert normalize_language("python3") == ScriptLanguage.PYTHON
        assert normalize_language("Python") == ScriptLanguage.PYTHON

    def test_bash_variants(self):
        assert normalize_language("sh") == ScriptLanguage.BASH
        assert normalize_language("shell") == ScriptLanguage.BASH
        assert normalize_language("bash") == ScriptLanguage.BASH
        assert normalize_language("zsh") == ScriptLanguage.BASH

    def test_empty_and_unknown_default_to_bash(self):
        assert normalize_language("") == ScriptLanguage.BASH
        assert normalize_language("unknown") == ScriptLanguage.BASH


class TestScanRequest:

    def test_minimal_construction(self):
        req = ScanRequest(script="print(1)", language=ScriptLanguage.PYTHON, tool_name="test")
        assert req.script == "print(1)"
        assert req.language == ScriptLanguage.PYTHON
        assert req.tool_name == "test"
        assert req.args == []
        assert req.cwd == ""
        assert req.env == {}
        assert req.tool_metadata == {}

    def test_full_construction(self):
        req = ScanRequest(
            script="ls -la",
            language=ScriptLanguage.BASH,
            tool_name="bash_tool",
            args=["-la", "/tmp"],
            cwd="/home/user",
            env={"PATH": "/usr/bin"},
            tool_metadata={"timeout": 30},
        )
        assert req.args == ["-la", "/tmp"]
        assert req.cwd == "/home/user"

    def test_default_target(self):
        req = ScanRequest(script="ls", language=ScriptLanguage.BASH, tool_name="t")
        assert req.target == ScanTarget.TOOL


class TestScanTarget:

    def test_values(self):
        assert ScanTarget.TOOL == "tool"
        assert ScanTarget.SKILL == "skill"
        assert ScanTarget.MCP_TOOL == "mcp_tool"
        assert ScanTarget.CODE_EXECUTOR == "code_executor"
        assert ScanTarget.FILE_TOOL == "file_tool"


class TestRiskType:

    def test_values(self):
        assert RiskType.DANGEROUS_FILE_OPERATION == "dangerous_file_operation"
        assert RiskType.NETWORK_EGRESS == "network_egress"
        assert RiskType.SYSTEM_COMMAND == "system_command"
        assert RiskType.DEPENDENCY_INSTALL == "dependency_install"
        assert RiskType.RESOURCE_ABUSE == "resource_abuse"
        assert RiskType.SECRET_EXFILTRATION == "secret_exfiltration"

    def test_count(self):
        assert len(RiskType) == 6


class TestSafetyFinding:

    def test_minimal_construction(self):
        f = SafetyFinding(
            rule_id="R001_BASH_RECURSIVE_DELETE",
            rule_name="Bash Recursive Delete",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf /home/user",
            recommendation="Remove the recursive delete flag or use a safer alternative.",
        )
        assert f.rule_id == "R001_BASH_RECURSIVE_DELETE"
        assert f.line is None
        assert f.column is None
        assert f.metadata == {}

    def test_full_construction(self):
        f = SafetyFinding(
            rule_id="R006_API_KEY_LEAK",
            rule_name="API Key Leak",
            risk_type=RiskType.SECRET_EXFILTRATION,
            risk_level=RiskLevel.HIGH,
            evidence='api_key = "sk-abc123"',
            line=42,
            column=10,
            recommendation="Use environment variables instead of hardcoded keys.",
            metadata={"cwe": "CWE-798"},
        )
        assert f.line == 42
        assert f.column == 10
        assert f.metadata["cwe"] == "CWE-798"


class TestSafetyReport:

    def test_minimal_construction(self):
        report = SafetyReport(
            tool_name="test_tool",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=5,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        assert report.tool_name == "test_tool"
        assert report.decision == Decision.ALLOW
        assert report.rule_ids == []
        assert report.findings == []
        assert report.telemetry_attributes == {}
        assert report.timestamp

    def test_with_findings(self):
        finding = SafetyFinding(
            rule_id="R001_BASH_RECURSIVE_DELETE",
            rule_name="Bash Recursive Delete",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf /",
            recommendation="Do not recursively delete.",
        )
        report = SafetyReport(
            tool_name="dangerous_tool",
            decision=Decision.DENY,
            risk_level=RiskLevel.CRITICAL,
            blocked=True,
            sanitized=False,
            duration_ms=12,
            language=ScriptLanguage.BASH,
            target=ScanTarget.TOOL,
            rule_ids=["R001_BASH_RECURSIVE_DELETE"],
            summary="Critical: recursive delete detected.",
            findings=[finding],
        )
        assert len(report.findings) == 1
        assert report.blocked is True

    def test_telemetry_attributes(self):
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
                "tool.safety.risk_level": "low",
            },
        )
        assert report.telemetry_attributes["tool.safety.decision"] == "allow"


class TestRiskOrder:

    def test_values(self):
        assert risk_order(RiskLevel.LOW) == 0
        assert risk_order(RiskLevel.MEDIUM) == 1
        assert risk_order(RiskLevel.HIGH) == 2
        assert risk_order(RiskLevel.CRITICAL) == 3

    def test_monotonic(self):
        levels = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        for i in range(len(levels) - 1):
            assert risk_order(levels[i]) < risk_order(levels[i + 1])


class TestDecisionOrder:

    def test_values(self):
        assert decision_order(Decision.ALLOW) == 0
        assert decision_order(Decision.NEEDS_HUMAN_REVIEW) == 1
        assert decision_order(Decision.DENY) == 2


class TestMaxRiskLevel:

    def test_empty(self):
        assert max_risk_level([]) == RiskLevel.LOW

    def test_single(self):
        f = SafetyFinding(
            rule_id="R001_TEST",
            rule_name="T",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.HIGH,
            evidence="e",
            recommendation="r",
        )
        assert max_risk_level([f]) == RiskLevel.HIGH

    def test_returns_highest(self):
        low = SafetyFinding(
            rule_id="L",
            rule_name="L",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.LOW,
            evidence="e",
            recommendation="r",
        )
        critical = SafetyFinding(
            rule_id="C",
            rule_name="C",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="e",
            recommendation="r",
        )
        assert max_risk_level([low, critical]) == RiskLevel.CRITICAL


class TestAggregateDecision:

    def test_empty(self):
        assert aggregate_decision([]) == Decision.ALLOW

    def test_critical_denies(self):
        f = SafetyFinding(
            rule_id="R",
            rule_name="R",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="e",
            recommendation="r",
        )
        assert aggregate_decision([f]) == Decision.DENY

    def test_high_denies(self):
        f = SafetyFinding(
            rule_id="R",
            rule_name="R",
            risk_type=RiskType.SECRET_EXFILTRATION,
            risk_level=RiskLevel.HIGH,
            evidence="e",
            recommendation="r",
        )
        assert aggregate_decision([f]) == Decision.DENY

    def test_medium_needs_review(self):
        f = SafetyFinding(
            rule_id="R",
            rule_name="R",
            risk_type=RiskType.NETWORK_EGRESS,
            risk_level=RiskLevel.MEDIUM,
            evidence="e",
            recommendation="r",
        )
        assert aggregate_decision([f]) == Decision.NEEDS_HUMAN_REVIEW

    def test_low_allows(self):
        f = SafetyFinding(
            rule_id="R",
            rule_name="R",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.LOW,
            evidence="e",
            recommendation="r",
        )
        assert aggregate_decision([f]) == Decision.ALLOW

    def test_mixed_uses_highest(self):
        low = SafetyFinding(
            rule_id="L",
            rule_name="L",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.LOW,
            evidence="e",
            recommendation="r",
        )
        critical = SafetyFinding(
            rule_id="C",
            rule_name="C",
            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            evidence="e",
            recommendation="r",
        )
        assert aggregate_decision([low, critical]) == Decision.DENY
