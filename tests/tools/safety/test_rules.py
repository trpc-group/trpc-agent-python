# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety import DEFAULT_RULE_DEFINITIONS
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import RiskType
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import apply_rule_policy
from trpc_agent_sdk.tools.safety import get_rule_definition
from trpc_agent_sdk.tools.safety import is_rule_enabled
from trpc_agent_sdk.tools.safety import iter_rule_definitions
from trpc_agent_sdk.tools.safety import make_finding
from trpc_agent_sdk.tools.safety import merge_findings
from trpc_agent_sdk.tools.safety import should_block_decision

EXPECTED_RULE_IDS = {
    "FILE_RECURSIVE_DELETE",
    "FILE_SYSTEM_OVERWRITE",
    "FILE_SENSITIVE_READ",
    "FILE_FORBIDDEN_PATH_ACCESS",
    "NET_NON_WHITELIST_EGRESS",
    "NET_DYNAMIC_EGRESS_REVIEW",
    "PROC_OS_SYSTEM",
    "PROC_SUBPROCESS_SHELL",
    "PROC_SHELL_PIPE_OR_CHAIN",
    "PROC_BACKGROUND_PROCESS",
    "PROC_PRIVILEGE_ESCALATION",
    "POLICY_DENIED_COMMAND",
    "DEP_PIP_INSTALL",
    "DEP_NPM_INSTALL",
    "DEP_SYSTEM_INSTALL",
    "RES_INFINITE_LOOP",
    "RES_FORK_BOMB",
    "RES_LONG_SLEEP",
    "RES_LARGE_WRITE",
    "LEAK_SECRET_LITERAL",
    "LEAK_ENV_SECRET",
    "PARSER_FALLBACK_USED",
}


class TestRuleCatalog:
    """Test built-in rule metadata."""

    def test_catalog_contains_expected_rule_ids(self):
        assert set(DEFAULT_RULE_DEFINITIONS) == EXPECTED_RULE_IDS
        assert [rule.rule_id for rule in iter_rule_definitions()] == list(DEFAULT_RULE_DEFINITIONS)

    def test_catalog_covers_required_risk_types(self):
        risk_types = {rule.risk_type for rule in DEFAULT_RULE_DEFINITIONS.values()}

        assert RiskType.FILE_OPERATION in risk_types
        assert RiskType.NETWORK_EGRESS in risk_types
        assert RiskType.PROCESS_EXECUTION in risk_types
        assert RiskType.DEPENDENCY_INSTALL in risk_types
        assert RiskType.RESOURCE_ABUSE in risk_types
        assert RiskType.SENSITIVE_LEAK in risk_types
        assert RiskType.POLICY_VIOLATION in risk_types
        assert RiskType.PARSER_WARNING in risk_types

    def test_every_rule_has_message_and_recommendation(self):
        for rule in DEFAULT_RULE_DEFINITIONS.values():
            assert rule.message.strip()
            assert rule.recommendation.strip()

    def test_get_unknown_rule_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown safety rule"):
            get_rule_definition("MISSING_RULE")


class TestRulePolicy:
    """Test policy overrides over rule definitions."""

    def test_rule_enabled_defaults_true_and_can_be_disabled(self):
        policy = SafetyPolicy(rules={"FILE_SENSITIVE_READ": {"enabled": False}})

        assert is_rule_enabled("FILE_RECURSIVE_DELETE", policy) is True
        assert is_rule_enabled("FILE_SENSITIVE_READ", policy) is False

    def test_apply_rule_policy_overrides_decision_and_risk_level(self):
        policy = SafetyPolicy(rules={
            "PROC_SUBPROCESS_SHELL": {
                "decision": "deny",
                "risk_level": "critical",
            }
        })
        original = get_rule_definition("PROC_SUBPROCESS_SHELL")

        overridden = apply_rule_policy(original, policy)

        assert original.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert original.risk_level == RiskLevel.HIGH
        assert overridden.decision == SafetyDecision.DENY
        assert overridden.risk_level == RiskLevel.CRITICAL

    def test_make_finding_applies_policy_and_redacts_evidence(self):
        policy = SafetyPolicy(
            max_evidence_chars=35,
            rules={
                "LEAK_SECRET_LITERAL": {
                    "decision": "needs_human_review",
                    "risk_level": "medium",
                }
            },
        )

        finding = make_finding(
            "LEAK_SECRET_LITERAL",
            "token=cleartext-value-that-should-not-survive",
            policy,
            metadata={"source": "unit-test"},
        )

        assert finding.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert finding.risk_level == RiskLevel.MEDIUM
        assert "[REDACTED]" in finding.evidence
        assert "cleartext-value" not in finding.evidence
        assert len(finding.evidence) <= policy.max_evidence_chars
        assert finding.redacted is True
        assert finding.metadata == {"source": "unit-test"}


class TestDecisionMerge:
    """Test finding merge and blocking decisions."""

    def test_merge_findings_empty_defaults_allow_low(self):
        assert merge_findings([]) == (SafetyDecision.ALLOW, RiskLevel.LOW)

    def test_merge_findings_denies_and_uses_highest_risk(self):
        policy = SafetyPolicy()
        findings = [
            make_finding("PROC_OS_SYSTEM", "os.system('ls')", policy),
            make_finding("FILE_RECURSIVE_DELETE", "rm -rf ~/.ssh", policy),
        ]

        assert merge_findings(findings) == (SafetyDecision.DENY, RiskLevel.CRITICAL)

    def test_merge_findings_review_when_no_deny(self):
        policy = SafetyPolicy()
        findings = [
            make_finding("PROC_OS_SYSTEM", "os.system('ls')", policy),
            make_finding("RES_LONG_SLEEP", "sleep 9999", policy),
        ]

        assert merge_findings(findings) == (SafetyDecision.NEEDS_HUMAN_REVIEW, RiskLevel.MEDIUM)

    def test_should_block_decision_respects_review_policy(self):
        blocking_policy = SafetyPolicy(review_blocks_execution=True)
        nonblocking_policy = SafetyPolicy(review_blocks_execution=False)

        assert should_block_decision(SafetyDecision.DENY, nonblocking_policy) is True
        assert should_block_decision(SafetyDecision.NEEDS_HUMAN_REVIEW, blocking_policy) is True
        assert should_block_decision(SafetyDecision.NEEDS_HUMAN_REVIEW, nonblocking_policy) is False
        assert should_block_decision(SafetyDecision.ALLOW, blocking_policy) is False
