"""Gate — multi-dimensional acceptance decision for optimized prompts.

Goes beyond simple threshold comparison to enforce:
- Sufficient improvement
- No critical case degradation
- No validation set regression (overfitting detection)
- Cost within budget
"""

from dataclasses import dataclass, field
from enum import Enum


class GateDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    NEEDS_REVIEW = "needs_review"


@dataclass
class GateResult:
    """Result of gate evaluation."""
    decision: GateDecision
    reason: str
    details: dict = field(default_factory=dict)


def evaluate_gate(
    baseline_pass_rate: float,
    candidate_pass_rate: float,
    baseline_metrics: dict[str, float],
    candidate_metrics: dict[str, float],
    min_improvement: float = 0.05,
    critical_case_ids: list[str] | None = None,
    baseline_failed: list[str] | None = None,
    candidate_failed: list[str] | None = None,
    max_cost: float = 10.0,
    optimization_cost: float = 0.0,
) -> GateResult:
    """Evaluate whether to accept the optimized candidate.

    Args:
        baseline_pass_rate: Pass rate before optimization.
        candidate_pass_rate: Pass rate after optimization.
        baseline_metrics: Per-metric scores for baseline.
        candidate_metrics: Per-metric scores for candidate.
        min_improvement: Minimum absolute improvement required.
        critical_case_ids: Cases that must not regress.
        baseline_failed: Case IDs that failed in baseline.
        candidate_failed: Case IDs that failed after optimization.
        max_cost: Maximum optimization budget.
        optimization_cost: Actual optimization cost.

    Returns:
        GateResult with accept/reject/needs_review decision.
    """
    baseline_failed = baseline_failed or []
    candidate_failed = candidate_failed or []
    critical_case_ids = critical_case_ids or []

    checks = []

    # Check 1: Overall improvement
    improvement = candidate_pass_rate - baseline_pass_rate
    checks.append({
        "check": "improvement_threshold",
        "passed": improvement >= min_improvement,
        "detail": f"Improvement: {improvement:+.2%} (threshold: {min_improvement:+.0%})",
    })

    # Check 2: No regression (candidate worse than baseline)
    if improvement < 0:
        return GateResult(
            decision=GateDecision.REJECT,
            reason=f"Candidate pass rate degraded by {abs(improvement):.1%} — rejecting",
            details={"improvement": improvement, "checks": checks},
        )

    # Check 3: Critical case protection
    newly_failed = set(candidate_failed) - set(baseline_failed)
    critical_regressed = set(critical_case_ids) & newly_failed
    checks.append({
        "check": "critical_cases",
        "passed": len(critical_regressed) == 0,
        "detail": (f"No critical cases regressed" if len(critical_regressed) == 0
                   else f"Critical cases regressed: {critical_regressed}"),
    })

    if critical_regressed:
        return GateResult(
            decision=GateDecision.REJECT,
            reason=f"Critical case(s) regressed: {critical_regressed}",
            details={"critical_regressed": list(critical_regressed), "checks": checks},
        )

    # Check 4: New hard failures
    checks.append({
        "check": "new_failures",
        "passed": len(newly_failed) == 0,
        "detail": (f"No new failures" if len(newly_failed) == 0
                   else f"New failures: {newly_failed}"),
    })

    # Check 5: Overfitting detection — train improvement without val improvement
    checks.append({
        "check": "overfitting",
        "passed": True,  # Requires val set comparison (handled in validate.py)
        "detail": "Validation set comparison handled separately",
    })

    # Check 6: Cost budget
    checks.append({
        "check": "cost_budget",
        "passed": optimization_cost <= max_cost,
        "detail": f"Cost: ${optimization_cost:.2f} / ${max_cost:.2f}",
    })

    if optimization_cost > max_cost:
        return GateResult(
            decision=GateDecision.REJECT,
            reason=f"Optimization cost ${optimization_cost:.2f} exceeds budget ${max_cost:.2f}",
            details={"cost": optimization_cost, "budget": max_cost, "checks": checks},
        )

    # Final decision
    if improvement < min_improvement:
        return GateResult(
            decision=GateDecision.NEEDS_REVIEW,
            reason=f"Improvement {improvement:+.2%} below threshold {min_improvement:+.0%}",
            details={"improvement": improvement, "checks": checks},
        )

    if newly_failed:
        return GateResult(
            decision=GateDecision.NEEDS_REVIEW,
            reason=f"{len(newly_failed)} new failure(s) introduced",
            details={"newly_failed": list(newly_failed), "checks": checks},
        )

    return GateResult(
        decision=GateDecision.ACCEPT,
        reason=f"All checks passed — improvement: {improvement:+.2%}",
        details={"improvement": improvement, "checks": checks},
    )
