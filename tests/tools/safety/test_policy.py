# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import RiskType
from trpc_agent_sdk.tools.safety import RulePolicy
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScanFinding
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import default_safety_policy
from trpc_agent_sdk.tools.safety import load_safety_policy
from trpc_agent_sdk.tools.safety import resolve_safety_policy


class ScannerWithPolicy:
    def __init__(self, policy: SafetyPolicy | None = None):
        self.policy = policy


class ScannerWithoutPolicy:
    pass


class TestSafetyPolicy:
    """Test safety policy defaults and YAML loading."""

    def test_default_policy_values(self):
        policy = default_safety_policy()

        assert policy.name == "default"
        assert policy.mode == "standard"
        assert policy.fail_closed is True
        assert policy.review_blocks_execution is True
        assert policy.allowed_domains == []
        assert policy.allowed_commands == []
        assert ".env" in policy.denied_paths
        assert ".env.*" in policy.denied_paths
        assert "~/.ssh" in policy.denied_paths
        assert "*KEY*" in policy.sensitive_env_keys
        assert "*TOKEN*" in policy.sensitive_env_keys
        assert "*SECRET*" in policy.sensitive_env_keys
        assert "*PASSWORD*" in policy.sensitive_env_keys
        assert policy.max_timeout_seconds == 300
        assert policy.max_output_bytes == 1048576
        assert policy.max_script_lines == 2000
        assert policy.max_sleep_seconds == 3600
        assert policy.max_evidence_chars == 200

    def test_load_empty_yaml_returns_default_policy(self, tmp_path):
        policy_path = tmp_path / "empty.yaml"
        policy_path.write_text("", encoding="utf-8")

        policy = load_safety_policy(policy_path)

        assert policy == default_safety_policy()

    def test_load_yaml_overrides_policy_fields(self, tmp_path):
        policy_path = tmp_path / "tool_safety_policy.yaml"
        policy_path.write_text(
            """
name: custom-policy
mode: strict
fail_closed: false
review_blocks_execution: false
allowed_domains:
  - api.github.com
denied_paths:
  - custom.secret
allowed_commands:
  - python
  - pytest
rules:
  PROC_SUBPROCESS_SHELL:
    enabled: true
    decision: needs_human_review
    risk_level: high
  FILE_SENSITIVE_READ:
    enabled: false
""",
            encoding="utf-8",
        )

        policy = load_safety_policy(policy_path)

        assert policy.name == "custom-policy"
        assert policy.mode == "strict"
        assert policy.fail_closed is False
        assert policy.review_blocks_execution is False
        assert policy.allowed_domains == ["api.github.com"]
        assert policy.denied_paths == ["custom.secret"]
        assert policy.allowed_commands == ["python", "pytest"]
        assert policy.rules["PROC_SUBPROCESS_SHELL"] == RulePolicy(
            enabled=True,
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.HIGH,
        )
        assert policy.rules["FILE_SENSITIVE_READ"] == RulePolicy(enabled=False)

    def test_load_invalid_yaml_raises_value_error(self, tmp_path):
        policy_path = tmp_path / "broken.yaml"
        policy_path.write_text("name: [unterminated", encoding="utf-8")

        with pytest.raises(ValueError, match="Invalid safety policy YAML"):
            load_safety_policy(policy_path)

    def test_load_non_mapping_yaml_raises_value_error(self, tmp_path):
        policy_path = tmp_path / "list.yaml"
        policy_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

        with pytest.raises(ValueError, match="must contain a mapping"):
            load_safety_policy(policy_path)

    def test_resolve_safety_policy_precedence(self, tmp_path):
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text("name: path-policy\n", encoding="utf-8")
        scanner_policy = SafetyPolicy(name="scanner-policy")
        explicit_policy = SafetyPolicy(name="explicit-policy")

        assert resolve_safety_policy(scanner=ScannerWithPolicy(scanner_policy)) is scanner_policy
        assert resolve_safety_policy(
            scanner=ScannerWithoutPolicy(),
            policy=explicit_policy,
            policy_path=policy_path,
        ) is explicit_policy
        assert resolve_safety_policy(scanner=ScannerWithoutPolicy(), policy_path=policy_path).name == "path-policy"
        assert resolve_safety_policy(policy=explicit_policy, policy_path=policy_path) is explicit_policy
        assert resolve_safety_policy(policy_path=policy_path).name == "path-policy"
        assert resolve_safety_policy().name == "default"


class TestSafetyTypes:
    """Test Phase 1 safety data contracts."""

    def test_scan_target_defaults_and_dump(self):
        target = ScanTarget(command="echo hello", language=ScriptLanguage.SHELL)
        dumped = target.model_dump(mode="json")

        assert dumped["command"] == "echo hello"
        assert dumped["language"] == "shell"
        assert dumped["args"] == []
        assert dumped["env"] == {}
        assert dumped["tool_metadata"] == {}

    def test_finding_report_and_audit_event_dump(self):
        finding = ScanFinding(
            rule_id="FILE_SENSITIVE_READ",
            risk_type=RiskType.FILE_OPERATION,
            risk_level=RiskLevel.CRITICAL,
            decision=SafetyDecision.DENY,
            message="Sensitive file access detected.",
            evidence="open('.env')",
            recommendation="Remove direct reads of sensitive credential files.",
            redacted=False,
        )
        report = SafetyReport(
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.CRITICAL,
            findings=[finding],
            elapsed_ms=1.5,
            redacted=False,
            blocked=True,
            language=ScriptLanguage.PYTHON,
        )
        event = SafetyAuditEvent(
            tool_name="workspace_exec",
            decision=report.decision,
            risk_level=report.risk_level,
            rule_ids=[finding.rule_id],
            elapsed_ms=report.elapsed_ms,
            redacted=report.redacted,
            blocked=report.blocked,
            language=report.language,
            finding_count=len(report.findings),
        )

        report_dump = report.model_dump(mode="json")
        event_dump = event.model_dump(mode="json")

        assert report_dump["decision"] == "deny"
        assert report_dump["risk_level"] == "critical"
        assert report_dump["scanner_version"] == "0.1.0"
        assert report_dump["policy_name"] == "default"
        assert report_dump["findings"][0]["rule_id"] == "FILE_SENSITIVE_READ"
        assert event_dump["tool_name"] == "workspace_exec"
        assert event_dump["rule_ids"] == ["FILE_SENSITIVE_READ"]
        assert event_dump["scanner_version"] == "0.1.0"

    def test_policy_model_validate_converts_rule_enums(self):
        policy = SafetyPolicy.model_validate({
            "rules": {
                "FILE_RECURSIVE_DELETE": {
                    "decision": "deny",
                    "risk_level": "critical",
                }
            }
        })

        rule = policy.rules["FILE_RECURSIVE_DELETE"]
        assert rule.decision == SafetyDecision.DENY
        assert rule.risk_level == RiskLevel.CRITICAL
