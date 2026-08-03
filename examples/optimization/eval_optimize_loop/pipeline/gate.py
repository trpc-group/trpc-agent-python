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
    validation_new_failures: int = 0,
    validation_new_failed: list[str] | None = None,
) -> GateResult:
    """Evaluate whether to accept the optimized candidate.

    Args:
        baseline_pass_rate: Pass rate before optimization.
        candidate_pass_rate: Pass rate after optimization.
        baseline_metrics: Per-metric scores for baseline（仅用于审计记录，不参与决策）。
        candidate_metrics: Per-metric scores for candidate（仅用于审计记录，不参与决策）。
        min_improvement: Minimum absolute improvement required.
        critical_case_ids: Cases that must not regress (on train or validation).
        baseline_failed: Case IDs that failed in baseline.
        candidate_failed: Case IDs that failed after optimization.
        max_cost: Maximum optimization budget.
        optimization_cost: Actual optimization cost.
        validation_new_failures: Candidate 在验证集上新增的失败数（过拟合检测）。
        validation_new_failed: 在验证集上新增失败的 case id 列表，用于关键 case 保护。

    Returns:
        GateResult with accept/reject/needs_review decision.
    """
    baseline_failed = baseline_failed or []
    candidate_failed = candidate_failed or []
    critical_case_ids = critical_case_ids or []

    # Compute all checks up-front so every decision path carries full audit
    # detail (no early return drops the remaining checks).
    improvement = candidate_pass_rate - baseline_pass_rate
    newly_failed = set(candidate_failed) - set(baseline_failed)
    critical_train_regressed = set(critical_case_ids) & newly_failed
    critical_val_regressed = set(critical_case_ids) & set(validation_new_failed or [])
    critical_regressed = critical_train_regressed | critical_val_regressed

    checks = [
        {
            "check": "improvement_threshold",
            "passed": improvement >= min_improvement,
            "detail": f"Improvement: {improvement:+.2%} (threshold: {min_improvement:+.0%})",
        },
        {
            "check": "no_degradation",
            "passed": improvement >= 0,
            "detail": (f"No regression" if improvement >= 0
                       else f"Candidate pass rate degraded by {abs(improvement):.1%}"),
        },
        {
            "check": "critical_cases",
            "passed": len(critical_regressed) == 0,
            "detail": (f"No critical cases regressed" if len(critical_regressed) == 0
                       else f"Critical cases regressed — train: {sorted(critical_train_regressed) or 'none'}, "
                            f"validation: {sorted(critical_val_regressed) or 'none'}"),
        },
        {
            "check": "new_failures",
            "passed": len(newly_failed) == 0,
            "detail": (f"No new failures" if len(newly_failed) == 0
                       else f"New failures: {newly_failed}"),
        },
        {
            "check": "overfitting",
            "passed": validation_new_failures == 0,
            "detail": (f"No validation regression"
                       if validation_new_failures == 0
                       else f"Candidate introduces {validation_new_failures} new failure(s) on validation set"),
        },
        {
            "check": "cost_budget",
            "passed": optimization_cost <= max_cost,
            "detail": f"Cost: ${optimization_cost:.2f} / ${max_cost:.2f}",
        },
    ]

    # Decisions (kept in original priority order)
    if improvement < 0:
        return GateResult(
            decision=GateDecision.REJECT,
            reason=f"Candidate pass rate degraded by {abs(improvement):.1%} — rejecting",
            details={"improvement": improvement, "checks": checks},
        )

    if critical_regressed:
        return GateResult(
            decision=GateDecision.REJECT,
            reason=f"Critical case(s) regressed — train: {sorted(critical_train_regressed) or 'none'}, "
                   f"validation: {sorted(critical_val_regressed) or 'none'}",
            details={
                "critical_regressed": list(critical_regressed),
                "critical_train_regressed": list(critical_train_regressed),
                "critical_val_regressed": list(critical_val_regressed),
                "checks": checks,
            },
        )

    if validation_new_failures > 0:
        return GateResult(
            decision=GateDecision.REJECT,
            reason=f"Overfitting: candidate introduces {validation_new_failures} new failure(s) on validation set",
            details={"validation_new_failures": validation_new_failures, "checks": checks},
        )

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
