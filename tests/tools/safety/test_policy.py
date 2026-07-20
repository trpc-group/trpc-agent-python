# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for SafetyPolicy — Phase 1 capability."""

from __future__ import annotations

import os

import pytest

from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import SafetyDecision

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def policy() -> SafetyPolicy:
    """Load the default policy file for testing."""
    policy_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "trpc_agent_sdk",
        "tools",
        "safety",
        "tool_safety_policy.yaml",
    )
    return SafetyPolicy.from_file(policy_path)


# ── SafetyPolicy Tests ───────────────────────────────────────────────────


class TestSafetyPolicy:
    """Tests for policy file loading and query methods."""

    def test_load_from_file(self, policy: SafetyPolicy):
        """Should load 7 rules from the default policy file."""
        assert len(policy.rules) == 7

    def test_all_rules_present(self, policy: SafetyPolicy):
        """All 7 risk rules should be present."""
        rule_names = [
            "dangerous_file_operations",
            "sensitive_file_read",
            "network_egress",
            "process_execution",
            "dependency_installation",
            "resource_abuse",
            "sensitive_info_leak",
        ]
        for name in rule_names:
            assert name in policy.rules, f"Missing rule: {name}"

    def test_whitelists_populated(self, policy: SafetyPolicy):
        """Whitelists should have entries."""
        assert len(policy.allowed_domains) > 0
        assert len(policy.allowed_commands) > 0
        assert len(policy.forbidden_paths) > 0

    def test_domain_allowed(self, policy: SafetyPolicy):
        """Whitelisted domain should be allowed."""
        assert policy.is_domain_allowed("api.openai.com") is True

    def test_domain_not_allowed(self, policy: SafetyPolicy):
        """Non-whitelisted domain should be rejected."""
        assert policy.is_domain_allowed("evil.com") is False

    def test_path_forbidden(self, policy: SafetyPolicy):
        """Forbidden path should be detected."""
        assert policy.is_path_forbidden(".env") is True
        assert policy.is_path_forbidden("~/.ssh/id_rsa") is True
        assert policy.is_path_forbidden("/etc/passwd") is True

    def test_path_not_forbidden(self, policy: SafetyPolicy):
        """Normal path should not be forbidden."""
        assert policy.is_path_forbidden("/tmp/test.txt") is False
        assert policy.is_path_forbidden("/home/user/file.py") is False

    def test_command_allowed(self, policy: SafetyPolicy):
        """Whitelisted command should be allowed."""
        assert policy.is_command_allowed("ls") is True
        assert policy.is_command_allowed("cat") is True

    def test_command_not_allowed(self, policy: SafetyPolicy):
        """Non-whitelisted command should be rejected."""
        assert policy.is_command_allowed("dd") is False

    def test_default_decision(self, policy: SafetyPolicy):
        """Default decision should be NEEDS_HUMAN_REVIEW."""
        assert policy.default_decision == SafetyDecision.NEEDS_HUMAN_REVIEW

    def test_r001_config(self, policy: SafetyPolicy):
        """R001: dangerous_file_operations should be DENY / CRITICAL."""
        rule = policy.rules["dangerous_file_operations"]
        assert rule.enabled is True
        assert rule.decision == SafetyDecision.DENY
        assert rule.risk_level == RiskLevel.CRITICAL

    def test_r003_has_check_domains(self, policy: SafetyPolicy):
        """R003: network_egress should have check_domains enabled."""
        rule = policy.rules["network_egress"]
        assert rule.check_domains is True

    def test_load_from_dict(self):
        """Should load policy from a dict."""
        policy = SafetyPolicy.from_dict({
            "allowed_domains": ["test.com"],
            "rules": {
                "test_rule": {
                    "enabled": True,
                    "decision": "deny",
                    "risk_level": "high",
                    "patterns": ["test"],
                },
            },
        })
        assert policy.is_domain_allowed("test.com") is True
        assert "test_rule" in policy.rules
        assert policy.rules["test_rule"].enabled is True

    def test_rule_not_found_returns_none(self, policy: SafetyPolicy):
        """get_rule for non-existent rule should return None."""
        assert policy.get_rule("non_existent_rule") is None
