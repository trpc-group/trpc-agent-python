# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Conservative aggregation of findings into a single SafetyReport."""
from __future__ import annotations

from trpc_agent_sdk.tools.safety._policy import Policy
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import Finding
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import SafetyReport


def aggregate(findings: list[Finding], policy: Policy) -> SafetyReport:
    """Merge findings into one report.

    Rule-level decisions win; policy thresholds act as a fallback so that an
    UNDECIDED rule whose risk crosses a threshold still resolves. Unknown
    cases are never silently allowed (issue: "do not let all uncertain cases
    through").
    """
    if not findings:
        return SafetyReport(decision=Decision.ALLOW, risk_level=RiskLevel.NONE)

    max_risk = max(f.risk_level for f in findings)

    decision = _decide(findings, max_risk, policy)
    recommendation = _recommend(decision, findings)
    return SafetyReport(
        decision=decision,
        risk_level=max_risk,
        findings=findings,
        recommendation=recommendation,
    )


def _decide(findings: list[Finding], max_risk: RiskLevel, policy: Policy) -> Decision:
    # Rule-level explicit decisions first.
    if any(f.rule_decision == Decision.DENY for f in findings):
        return Decision.DENY
    if any(f.rule_decision == Decision.NEEDS_REVIEW for f in findings):
        return Decision.NEEDS_REVIEW
    # Threshold fallback (covers UNDECIDED rule decisions).
    if max_risk >= policy.deny_risk_level:
        return Decision.DENY
    if max_risk >= policy.review_risk_level:
        return Decision.NEEDS_REVIEW
    return Decision.ALLOW


def _recommend(decision: Decision, findings: list[Finding]) -> str:
    if decision == Decision.ALLOW:
        return "No blocking risks detected; proceeding."
    ids = ", ".join(sorted({f.rule_id for f in findings}))
    if decision == Decision.DENY:
        return f"Blocked by safety rules: {ids}. Fix or allowlist before execution."
    return f"Needs human review for: {ids}."
