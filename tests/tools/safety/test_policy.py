# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for safety policy module."""

import tempfile
from pathlib import Path

from trpc_agent_sdk.tools.safety._policy import (
    SafetyPolicy,
    PolicyRuleConfig,
)
from trpc_agent_sdk.tools.safety._types import RiskType, Decision, RiskLevel

MINIMAL_YAML = """
version: "1.0"
max_script_size_bytes: 1048576
max_scan_time_ms: 1000
default_decision: deny
rules:
  - rule_id: DANGEROUS_DELETE_001
    enabled: true
    risk_type: dangerous_file_operation
    severity: critical
    decision: deny
whitelist:
  domains:
    - api.example.com
  commands:
    - ls
  paths:
    - /tmp/
blocklist:
  paths:
    - ~/.ssh
  commands:
    - sudo
"""


class TestPolicyLoading:

    def test_load_from_yaml_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(MINIMAL_YAML)
            policy_path = f.name
        try:
            policy = SafetyPolicy.load(Path(policy_path))
            assert policy.version == "1.0"
            assert policy.max_script_size_bytes == 1048576
            assert policy.default_decision == Decision.DENY
            assert len(policy.rules) == 1
        finally:
            Path(policy_path).unlink()

    def test_whitelist_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(MINIMAL_YAML)
            policy_path = f.name
        try:
            policy = SafetyPolicy.load(Path(policy_path))
            assert "api.example.com" in policy.whitelist.domains
            assert "ls" in policy.whitelist.commands
            assert "/tmp/" in policy.whitelist.paths
        finally:
            Path(policy_path).unlink()

    def test_blocklist_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(MINIMAL_YAML)
            policy_path = f.name
        try:
            policy = SafetyPolicy.load(Path(policy_path))
            assert "~/.ssh" in policy.blocklist.paths
            assert "sudo" in policy.blocklist.commands
        finally:
            Path(policy_path).unlink()


class TestPolicyRuleConfig:

    def test_enabled_rule(self):
        rule = PolicyRuleConfig(
            rule_id="TEST_001",
            enabled=True,
            risk_type=RiskType.DANGEROUS_FILE_OP,
            severity=RiskLevel.CRITICAL,
            decision=Decision.DENY,
        )
        assert rule.effective_decision() == Decision.DENY

    def test_disabled_rule_returns_allow(self):
        rule = PolicyRuleConfig(
            rule_id="TEST_001",
            enabled=False,
            risk_type=RiskType.DANGEROUS_FILE_OP,
            severity=RiskLevel.CRITICAL,
            decision=Decision.DENY,
        )
        assert rule.effective_decision() == Decision.ALLOW


class TestGetEnabledRules:

    def test_get_enabled_rules_filters_disabled(self):
        policy = SafetyPolicy(rules=[
            PolicyRuleConfig(
                rule_id="ENABLED_001",
                enabled=True,
                risk_type=RiskType.DANGEROUS_FILE_OP,
                severity=RiskLevel.CRITICAL,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="DISABLED_002",
                enabled=False,
                risk_type=RiskType.NETWORK_ACCESS,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
        ])
        enabled = policy.get_enabled_rules()
        assert len(enabled) == 1
        assert enabled[0].rule_id == "ENABLED_001"


class TestModifiedPolicyWithoutCodeChange:

    def test_changed_yaml_reflects_in_policy(self):
        yaml_a = MINIMAL_YAML
        yaml_b = MINIMAL_YAML.replace("max_scan_time_ms: 1000", "max_scan_time_ms: 5000")
        assert yaml_a != yaml_b

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_a)
            path_a = f.name
        try:
            policy_a = SafetyPolicy.load(Path(path_a))
            assert policy_a.max_scan_time_ms == 1000
        finally:
            Path(path_a).unlink()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_b)
            path_b = f.name
        try:
            policy_b = SafetyPolicy.load(Path(path_b))
            assert policy_b.max_scan_time_ms == 5000
        finally:
            Path(path_b).unlink()
