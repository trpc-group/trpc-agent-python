# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Single deterministic decision aggregator for tool safety reports."""

from __future__ import annotations

import time
from typing import Iterable

from ._models import AnalysisStatus
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._policy import SafetyPolicy
from ._rules import NON_RELAXABLE_REVIEW_RULE_IDS
from ._rules import RULE_SPECS

_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}
_ACTION_ORDER = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}


def aggregate_report(
    findings: Iterable[SafetyFinding],
    *,
    started: float,
    digest: str,
    policy: SafetyPolicy,
    analysis_status: AnalysisStatus = AnalysisStatus.COMPLETE,
    invocation_id: str | None = None,
    blocks_scanned: int = 1,
) -> SafetyReport:
    """Aggregate all facts once using the fixed deny-review-allow ordering."""

    collected = list(findings)
    if analysis_status != AnalysisStatus.COMPLETE:
        collected = [
            finding.model_copy(update={"action": SafetyDecision.NEEDS_HUMAN_REVIEW}) if
            (finding.rule_id in NON_RELAXABLE_REVIEW_RULE_IDS and finding.action == SafetyDecision.ALLOW) else finding
            for finding in collected
        ]
        if not any(finding.rule_id in NON_RELAXABLE_REVIEW_RULE_IDS for finding in collected):
            spec = RULE_SPECS["PARSE-001"]
            collected.append(
                SafetyFinding(
                    rule_id=spec.rule_id,
                    category=spec.category,
                    risk_level=spec.risk_level,
                    action=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    message=spec.message,
                    evidence=f"analysis_status={analysis_status.value}",
                    recommendation=spec.recommendation,
                ))

    unique: dict[tuple[str, str, int | None, int | None, str], SafetyFinding] = {}
    for finding in collected:
        key = (
            finding.rule_id,
            finding.block_id,
            finding.line_number,
            finding.column,
            finding.evidence,
        )
        previous = unique.get(key)
        if previous is None or (
                _ACTION_ORDER[finding.action],
                _RISK_ORDER[finding.risk_level],
        ) > (
                _ACTION_ORDER[previous.action],
                _RISK_ORDER[previous.risk_level],
        ):
            unique[key] = finding
    ordered = sorted(
        unique.values(),
        key=lambda finding: (
            -_ACTION_ORDER[finding.action],
            -_RISK_ORDER[finding.risk_level],
            finding.rule_id,
            finding.block_id,
            finding.line_number or 0,
            finding.column or 0,
        ),
    )
    if any(finding.action == SafetyDecision.DENY for finding in ordered):
        decision = SafetyDecision.DENY
    elif any(finding.action == SafetyDecision.NEEDS_HUMAN_REVIEW for finding in ordered):
        decision = SafetyDecision.NEEDS_HUMAN_REVIEW
    else:
        decision = SafetyDecision.ALLOW
    risk_level = max(
        (finding.risk_level for finding in ordered),
        key=lambda level: _RISK_ORDER[level],
        default=RiskLevel.NONE,
    )
    if ordered:
        primary = ordered[0]
        rule_id = primary.rule_id
        evidence = primary.evidence
        recommendation = primary.recommendation
    else:
        rule_id = "ALLOW-000"
        evidence = "analysis complete; no active policy finding"
        recommendation = "Execute with least privilege and runtime limits."
    relaxed = any(not override.enabled or override.action == SafetyDecision.ALLOW
                  for override in policy.rule_overrides.values())
    return SafetyReport(
        decision=decision,
        risk_level=risk_level,
        rule_id=rule_id,
        evidence=evidence,
        recommendation=recommendation,
        findings=ordered,
        duration_ms=(time.perf_counter() - started) * 1000,
        sanitized=True,
        policy_version=policy.version,
        policy_api_version=policy.api_version,
        policy_id=policy.policy_id,
        policy_relaxed=relaxed,
        input_sha256=digest,
        analysis_complete=analysis_status == AnalysisStatus.COMPLETE,
        analysis_status=analysis_status,
        invocation_id=invocation_id or f"scan-{digest[:16]}",
        blocks_scanned=blocks_scanned,
    )
