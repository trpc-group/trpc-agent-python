# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Stateless rule contract and deterministic decision aggregation."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any
from typing import Optional

from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._policy import SafetyPolicy

_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}
_DECISION_RANK = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}


@dataclass(frozen=True)
class NestedCandidate:
    """A statically extracted nested source candidate."""

    language: str
    script: str
    line_number: Optional[int] = None
    reason: str = "nested script"


@dataclass(frozen=True)
class ScanContext:
    """Read-only facts shared by all rules for one parsed source."""

    language: str
    source: str
    candidate_findings: tuple[SafetyFinding, ...] = ()
    nested_candidates: tuple[NestedCandidate, ...] = ()
    analysis_complete: bool = True
    failure_code: Optional[str] = None
    parse_count: int = 1
    details: Any = None


class SafetyRule(ABC):
    """A stateless, side-effect-free safety rule."""

    rule_id = "CUSTOM.UNSPECIFIED"
    languages: tuple[str, ...] = ("python", "shell")

    @abstractmethod
    def evaluate(self, context: ScanContext, policy: SafetyPolicy) -> tuple[SafetyFinding, ...]:
        """Read one context and return zero or more facts without side effects."""


class ContextFindingRule(SafetyRule):
    """Expose facts produced by a language context to the common pipeline."""

    rule_id = "BUILTIN.CONTEXT_FACTS"
    languages = ("*", )

    def evaluate(self, context: ScanContext, policy: SafetyPolicy) -> tuple[SafetyFinding, ...]:
        del policy
        return context.candidate_findings


@dataclass(frozen=True)
class AggregatedDecision:
    """Pure aggregation output used to build a public report."""

    decision: SafetyDecision
    risk_level: Optional[RiskLevel]
    findings: tuple[SafetyFinding, ...]
    reason: str
    failure_code: Optional[str] = None


def _finding_key(finding: SafetyFinding) -> tuple[Any, ...]:
    return (
        finding.rule_id,
        finding.category.value,
        -1 if finding.line_number is None else finding.line_number,
        -1 if finding.column_number is None else finding.column_number,
        -1 if finding.block_index is None else finding.block_index,
        finding.nested_path,
        finding.evidence,
    )


class DecisionAggregator:
    """Apply policy overrides and fixed decision precedence deterministically."""

    def aggregate(
        self,
        findings: tuple[SafetyFinding, ...] | list[SafetyFinding],
        policy: SafetyPolicy,
        *,
        forced_decision: Optional[SafetyDecision] = None,
        failure_code: Optional[str] = None,
    ) -> AggregatedDecision:
        prepared: list[SafetyFinding] = []
        per_finding_decisions: list[SafetyDecision] = []
        seen: set[tuple[Any, ...]] = set()

        for original in sorted(findings, key=_finding_key):
            rule_policy = policy.rule_policy(original.rule_id)
            if rule_policy is not None and not rule_policy.enabled and not original.hard_deny:
                continue
            finding = original
            if rule_policy is not None and rule_policy.risk_override is not None and not original.hard_deny:
                finding = original.model_copy(update={"risk_level": rule_policy.risk_override})
            key = _finding_key(finding)
            if key in seen:
                continue
            seen.add(key)
            prepared.append(finding)

            if finding.hard_deny:
                decision = SafetyDecision.DENY
            elif rule_policy is not None and rule_policy.decision_override is not None:
                decision = rule_policy.decision_override
            elif _RISK_RANK[finding.risk_level] >= _RISK_RANK[policy.deny_threshold]:
                decision = SafetyDecision.DENY
            elif _RISK_RANK[finding.risk_level] >= _RISK_RANK[policy.review_threshold]:
                decision = SafetyDecision.NEEDS_HUMAN_REVIEW
            else:
                decision = SafetyDecision.ALLOW
            per_finding_decisions.append(decision)

        if len(prepared) > policy.max_findings:
            prepared = prepared[:policy.max_findings]
            forced_decision = max(
                forced_decision or SafetyDecision.ALLOW,
                policy.failures.budget_exceeded,
                key=lambda item: _DECISION_RANK[item],
            )
            failure_code = failure_code or "finding_budget_exceeded"

        candidates = per_finding_decisions + ([forced_decision] if forced_decision else [])
        decision = max(candidates, key=lambda item: _DECISION_RANK[item]) if candidates else SafetyDecision.ALLOW
        risk_level = max((item.risk_level for item in prepared), key=lambda item: _RISK_RANK[item], default=None)
        if failure_code:
            reason = f"analysis incomplete: {failure_code}"
        elif not prepared:
            reason = "analysis completed without findings"
        else:
            reason = f"policy aggregated {len(prepared)} finding(s) to {decision.value}"
        return AggregatedDecision(
            decision=decision,
            risk_level=risk_level,
            findings=tuple(prepared),
            reason=reason,
            failure_code=failure_code,
        )
