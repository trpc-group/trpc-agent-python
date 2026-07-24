# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

from trpc_agent_sdk.tools.safety._decision import aggregate
from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import Finding
from trpc_agent_sdk.tools.safety._types import RiskLevel


def _f(rule_id, risk, decision):
    return Finding(rule_id=rule_id, risk_level=risk, rule_decision=decision,
                   evidence="x", recommendation="y", language="python")


def test_no_findings_allow():
    policy = load_policy()
    report = aggregate([], policy)
    assert report.decision == Decision.ALLOW
    assert report.risk_level == RiskLevel.NONE


def test_any_deny_wins():
    policy = load_policy()
    findings = [
        _f("tool-net-http", RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
        _f("tool-fs-recursive-delete", RiskLevel.HIGH, Decision.DENY),
    ]
    report = aggregate(findings, policy)
    assert report.decision == Decision.DENY


def test_review_when_no_deny():
    policy = load_policy()
    findings = [_f("tool-net-http", RiskLevel.MEDIUM, Decision.NEEDS_REVIEW)]
    report = aggregate(findings, policy)
    assert report.decision == Decision.NEEDS_REVIEW


def test_threshold_promotes_to_deny():
    # A finding with UNDECIDED rule_decision but HIGH risk -> DENY via threshold.
    policy = load_policy()
    findings = [_f("tool-x", RiskLevel.HIGH, Decision.UNDECIDED)]
    report = aggregate(findings, policy)
    assert report.decision == Decision.DENY


def test_low_risk_allows():
    policy = load_policy()
    findings = [_f("tool-x", RiskLevel.LOW, Decision.ALLOW)]
    report = aggregate(findings, policy)
    assert report.decision == Decision.ALLOW
