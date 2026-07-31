# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule plug-in, overrides, hard-deny, dedupe, and stable order tests."""

from __future__ import annotations

from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import SafetyCategory
from trpc_agent_sdk.safety import SafetyDecision
from trpc_agent_sdk.safety import SafetyFinding
from trpc_agent_sdk.safety import SafetyPolicy
from trpc_agent_sdk.safety import SafetyRule
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety._rule import ContextFindingRule
from trpc_agent_sdk.safety._rule import ScanContext


class FixedRule(SafetyRule):
    languages = ("python", )

    def __init__(self, rule_id: str, findings: tuple[SafetyFinding, ...]):
        self.rule_id = rule_id
        self._findings = findings

    def evaluate(self, context: ScanContext, policy: SafetyPolicy) -> tuple[SafetyFinding, ...]:
        del context, policy
        return self._findings


class FailingRule(SafetyRule):
    rule_id = "TEST.FAILING"
    languages = ("python", )

    def evaluate(self, context: ScanContext, policy: SafetyPolicy) -> tuple[SafetyFinding, ...]:
        del context, policy
        raise RuntimeError("must not escape")


def _finding(rule_id: str, risk: RiskLevel, *, hard: bool = False, line: int = 1) -> SafetyFinding:
    return SafetyFinding(
        rule_id=rule_id,
        category=SafetyCategory.PROCESS,
        risk_level=risk,
        message="fixed test fact",
        evidence="fixed",
        recommendation="review",
        line_number=line,
        hard_deny=hard,
    )


def _scan(policy: SafetyPolicy, rules: list[SafetyRule]):
    return SafetyScanner(policy, rules=rules).scan(SafetyScanRequest(script="value = 1", language="python"))


def test_each_risk_uses_thresholds_but_risk_is_separate():
    policy = SafetyPolicy.default()
    expected = {
        RiskLevel.LOW: SafetyDecision.ALLOW,
        RiskLevel.MEDIUM: SafetyDecision.NEEDS_HUMAN_REVIEW,
        RiskLevel.HIGH: SafetyDecision.DENY,
        RiskLevel.CRITICAL: SafetyDecision.DENY,
    }
    for risk, decision in expected.items():
        report = _scan(policy, [FixedRule(f"TEST.{risk.value}", (_finding(f"TEST.{risk.value}", risk), ))])
        assert report.risk_level is risk
        assert report.decision is decision
        assert report.execution_blocked is (decision is not SafetyDecision.ALLOW)


def test_disabled_rule_and_non_hard_decision_override():
    disabled = SafetyPolicy(schema_version="1", rules={"TEST.RULE": {"enabled": False}})
    overridden = SafetyPolicy(
        schema_version="1",
        rules={"TEST.RULE": {
            "decision_override": "allow"
        }},
    )
    rule = FixedRule("TEST.PRODUCER", (_finding("TEST.RULE", RiskLevel.HIGH), ))
    assert _scan(disabled, [rule]).decision is SafetyDecision.ALLOW
    assert _scan(overridden, [rule]).decision is SafetyDecision.ALLOW


def test_risk_override_changes_decision():
    policy = SafetyPolicy(schema_version="1", rules={"TEST.RULE": {"risk_override": "low"}})
    report = _scan(policy, [FixedRule("TEST.PRODUCER", (_finding("TEST.RULE", RiskLevel.HIGH), ))])
    assert report.risk_level is RiskLevel.LOW
    assert report.decision is SafetyDecision.ALLOW


def test_hard_deny_cannot_be_overridden_by_policy_or_allowlist():
    policy = SafetyPolicy(
        schema_version="1",
        allowed_commands=("danger", ),
        rules={"TEST.HARD": {
            "enabled": False,
            "risk_override": "low",
            "decision_override": "allow"
        }},
    )
    report = _scan(policy, [FixedRule("TEST.PRODUCER", (_finding("TEST.HARD", RiskLevel.CRITICAL, hard=True), ))])
    assert report.decision is SafetyDecision.DENY


def test_builtin_context_cannot_be_disabled_to_bypass_hard_deny():
    policy = SafetyPolicy(
        schema_version="1",
        rules={"BUILTIN.CONTEXT_FACTS": {
            "enabled": False
        }},
    )
    report = SafetyScanner(policy).scan(
        SafetyScanRequest(script="import os\nos.remove('/etc/hosts')", language="python"))
    assert report.decision is SafetyDecision.DENY
    assert "PY.FILESYSTEM.DESTRUCTIVE_DELETE" in report.rule_ids
    assert report.findings[0].hard_deny


def test_deny_beats_review_and_result_is_rule_order_independent():
    review = FixedRule("TEST.Z", (_finding("TEST.REVIEW", RiskLevel.MEDIUM, line=2), ))
    deny = FixedRule("TEST.A", (_finding("TEST.DENY", RiskLevel.HIGH, line=1), ))
    first = _scan(SafetyPolicy.default(), [review, deny])
    second = _scan(SafetyPolicy.default(), [deny, review])
    assert first.decision is SafetyDecision.DENY
    assert first.rule_ids == second.rule_ids
    assert first.findings == second.findings


def test_duplicate_findings_are_removed_and_order_is_stable():
    duplicate = _finding("TEST.DUP", RiskLevel.MEDIUM)
    rule = FixedRule("TEST.PRODUCER", (duplicate, duplicate, _finding("TEST.A", RiskLevel.LOW, line=2)))
    report = _scan(SafetyPolicy.default(), [rule])
    assert report.finding_count == 2
    assert [item.rule_id for item in report.findings] == ["TEST.A", "TEST.DUP"]


def test_rule_exception_is_fail_closed_not_swallowed_to_allow():
    report = _scan(SafetyPolicy.default(), [FailingRule(), ContextFindingRule()])
    assert report.decision is SafetyDecision.DENY
    assert report.failure_code == "scanner_internal_error"
    assert not report.analysis_complete
    assert "CORE.ANALYSIS.RULE_FAILURE" in report.rule_ids


def test_same_input_and_policy_are_deterministic_except_duration(scanner: SafetyScanner):
    request = SafetyScanRequest(script="import os\nos.remove('/etc/hosts')", language="python")
    first = scanner.scan(request).model_dump(exclude={"scan_duration_ms"})
    second = scanner.scan(request).model_dump(exclude={"scan_duration_ms"})
    assert first == second
