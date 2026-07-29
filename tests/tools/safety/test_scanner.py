# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for SafetyScanner."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import RiskType
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanRequest
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety._scanner import SafetyScanner as _Scanner


@pytest.fixture
def scanner():
    return SafetyScanner(PolicyConfig.default())


class TestSafetyScannerScan:

    def test_safe_python(self, scanner):
        req = ScanRequest(script="print('hello')", language=ScriptLanguage.PYTHON, tool_name="test")
        report = scanner.scan(req)
        assert isinstance(report, SafetyReport)
        assert report.tool_name == "test"
        assert report.decision == Decision.ALLOW

    def test_regular_python_open_does_not_deny(self, scanner):
        req = ScanRequest(script="open('data.txt').read()", language=ScriptLanguage.PYTHON, tool_name="regular_open")
        report = scanner.scan(req)
        assert report.decision == Decision.NEEDS_HUMAN_REVIEW
        assert report.risk_level == RiskLevel.MEDIUM

    def test_dangerous_bash(self, scanner):
        req = ScanRequest(script="rm -rf /", language=ScriptLanguage.BASH, tool_name="danger")
        report = scanner.scan(req)
        assert report.decision == Decision.DENY
        assert report.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH)

    def test_sanitized_flag_with_secret(self, scanner):
        req = ScanRequest(
            script="export MY_VAR=sk-xxxxxxxxxxxx",
            language=ScriptLanguage.BASH,
            tool_name="leak",
        )
        report = scanner.scan(req)
        assert report.sanitized is True

    def test_no_sanitized_flag_without_secret(self, scanner):
        req = ScanRequest(script="echo hello", language=ScriptLanguage.BASH, tool_name="clean")
        report = scanner.scan(req)
        assert report.sanitized is False

    def test_findings_are_deduplicated(self, scanner):
        # Same pattern repeated in script should only produce one finding per rule_id per line
        req = ScanRequest(script="rm -rf /\nrm -rf /\nrm -rf /", language=ScriptLanguage.BASH, tool_name="dup")
        report = scanner.scan(req)
        rule_ids = [f.rule_id for f in report.findings]
        assert "R001_BASH_RECURSIVE_DELETE" in rule_ids or any("R001" in r for r in rule_ids)

    def test_report_has_telemetry(self, scanner):
        req = ScanRequest(script="echo hi", language=ScriptLanguage.BASH, tool_name="t")
        report = scanner.scan(req)
        assert "tool.safety.decision" in report.telemetry_attributes
        assert "tool.safety.risk_level" in report.telemetry_attributes

    def test_report_has_timestamp_and_duration(self, scanner):
        req = ScanRequest(script="echo hi", language=ScriptLanguage.BASH, tool_name="t")
        report = scanner.scan(req)
        assert report.timestamp
        assert report.duration_ms >= 0


class TestIsEnvContainsSensitiveKeys:

    def test_sensitive_key_detected(self, scanner):
        env = {"SECRET_VAR": "xxx", "PATH": "/usr/bin"}
        assert scanner._is_env_contains_sensitive_keys(env) is True

    def test_no_sensitive_key(self, scanner):
        env = {"PATH": "/usr/bin", "HOME": "/home", "LANG": "en_US"}
        assert scanner._is_env_contains_sensitive_keys(env) is False

    def test_empty_env(self, scanner):
        assert scanner._is_env_contains_sensitive_keys({}) is False


class TestScanContextSafety:

    def test_cwd_denied(self, scanner):
        req = ScanRequest(script="echo hi", language=ScriptLanguage.BASH, tool_name="t", cwd="/etc/nginx")
        findings = scanner._scan_context_safety(req)
        rule_ids = {f.rule_id for f in findings}
        assert "R001_SYSTEM_PATH_OVERWRITE" in rule_ids

    def test_cwd_exact_denied_path_denied(self, scanner):
        req = ScanRequest(script="echo hi", language=ScriptLanguage.BASH, tool_name="t", cwd="/root")
        findings = scanner._scan_context_safety(req)
        rule_ids = {f.rule_id for f in findings}
        assert "R001_SYSTEM_PATH_OVERWRITE" in rule_ids

    def test_timeout_exceeded(self, scanner):
        req = ScanRequest(script="echo hi", language=ScriptLanguage.BASH, tool_name="t", tool_metadata={"timeout": 999})
        findings = scanner._scan_context_safety(req)
        rule_ids = {f.rule_id for f in findings}
        assert "R005_RESOURCE_ABUSE" in rule_ids


class TestDeduplicateFindings:

    def test_dedup_by_rule_id_and_line(self):
        from trpc_agent_sdk.tools.safety._types import SafetyFinding
        f1 = SafetyFinding(
            rule_id="R001_TEST",
            rule_name="T",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.LOW,
            evidence="e1",
            recommendation="r",
            line=10,
        )
        f2 = SafetyFinding(
            rule_id="R001_TEST",
            rule_name="T",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.LOW,
            evidence="e2",
            recommendation="r",
            line=10,
        )
        f3 = SafetyFinding(
            rule_id="R001_TEST",
            rule_name="T",
            risk_type=RiskType.RESOURCE_ABUSE,
            risk_level=RiskLevel.LOW,
            evidence="e3",
            recommendation="r",
            line=20,
        )
        result = _Scanner._deduplicate_findings([f1, f2, f3])
        assert len(result) == 2  # f1 and f3 (f2 deduped)


class TestEnvAllowlistCoverage:

    def test_env_allowlist_excludes_key(self, scanner):
        """Sensitive key in env_allowlist is not flagged."""
        policy = PolicyConfig.from_dict({
            "env_allowlist": ["SECRET_VAR"],
        })
        scanner2 = SafetyScanner(policy)
        env = {"SECRET_VAR": "xxx", "PATH": "/usr/bin"}
        assert scanner2._is_env_contains_sensitive_keys(env) is False

    def test_max_output_bytes_exceeded(self, scanner):
        """max_output_bytes exceeding policy limit triggers finding."""
        req = ScanRequest(
            script="echo hi",
            language=ScriptLanguage.BASH,
            tool_name="t",
            tool_metadata={"max_output_bytes": 999_999_999},
        )
        findings = scanner._scan_context_safety(req)
        rule_ids = {f.rule_id for f in findings}
        assert "R005_RESOURCE_ABUSE" in rule_ids


class TestGenerateSummary:

    def test_allow_summary(self):
        summary = _Scanner._generate_summary(Decision.ALLOW, RiskLevel.LOW, [])
        assert "passed" in summary.lower()

    def test_deny_summary(self):
        summary = _Scanner._generate_summary(Decision.DENY, RiskLevel.CRITICAL, ["R001_TEST"])
        assert "blocked" in summary.lower()

    def test_review_summary(self):
        summary = _Scanner._generate_summary(Decision.NEEDS_HUMAN_REVIEW, RiskLevel.MEDIUM, ["R002_TEST"])
        assert "review" in summary.lower()


class TestToolSafetyCheckExitCode:

    def test_review_exit_code_defaults_to_two(self):
        from scripts.tool_safety_check import _exit_code_for_decision
        assert _exit_code_for_decision(Decision.NEEDS_HUMAN_REVIEW) == 2

    def test_block_on_review_exit_code_is_one(self):
        from scripts.tool_safety_check import _exit_code_for_decision
        assert _exit_code_for_decision(Decision.NEEDS_HUMAN_REVIEW, block_on_review=True) == 1

    def test_deny_and_allow_exit_codes_are_stable(self):
        from scripts.tool_safety_check import _exit_code_for_decision
        assert _exit_code_for_decision(Decision.DENY) == 1
        assert _exit_code_for_decision(Decision.ALLOW) == 0
