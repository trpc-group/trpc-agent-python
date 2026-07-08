"""Monitoring summary helpers."""

from __future__ import annotations

from collections import Counter

from .models import Finding
from .models import FilterDecision
from .models import MonitoringSummary
from .models import SandboxRun


def build_monitoring_summary(
    *,
    total_duration_ms: int,
    stage_durations_ms: dict[str, int],
    sandbox_runs: list[SandboxRun],
    findings: list[Finding],
    warnings: list[Finding],
    needs_human_review: list[Finding],
    filter_decisions: list[FilterDecision],
    filter_decision_count: int,
    redaction_count: int,
    deduped_finding_count: int = 0,
    ignored_finding_count: int = 0,
) -> MonitoringSummary:
    all_items = findings + warnings + needs_human_review
    exception_distribution = Counter(run.exception_type for run in sandbox_runs if run.exception_type)
    return MonitoringSummary(
        total_duration_ms=total_duration_ms,
        sandbox_duration_ms=sum(run.duration_ms for run in sandbox_runs),
        stage_durations_ms=stage_durations_ms,
        risk_level=_risk_level(all_items, filter_decisions),
        tool_call_count=len(sandbox_runs),
        filter_decision_count=filter_decision_count,
        interception_count=sum(1 for decision in filter_decisions
                               if decision.decision in {"deny", "needs_human_review"}),
        filter_decision_distribution=dict(Counter(decision.decision for decision in filter_decisions)),
        finding_count=len(findings),
        warning_count=len(warnings),
        needs_human_review_count=len(needs_human_review),
        severity_distribution=dict(Counter(item.severity for item in all_items)),
        category_distribution=dict(Counter(item.category for item in all_items)),
        exception_distribution=dict(exception_distribution),
        redaction_count=redaction_count,
        deduped_finding_count=deduped_finding_count,
        ignored_finding_count=ignored_finding_count,
    )


def _risk_level(items: list[Finding], filter_decisions: list[FilterDecision]) -> str:
    rank = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
    severities = [item.severity for item in items]
    severities.extend(decision.severity for decision in filter_decisions
                      if decision.decision in {"deny", "needs_human_review"})
    if not severities:
        return "none"
    return max(severities, key=lambda severity: rank.get(severity, 0))
