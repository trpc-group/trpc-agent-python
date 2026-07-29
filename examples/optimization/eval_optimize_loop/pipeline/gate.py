# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Acceptance gate for candidate prompt deltas."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any

from .types import CaseDelta
from .types import GateConfig
from .types import GateDecision
from .types import GateRuleResult
from .types import OptimizationDelta
from .types import OptimizationRunRecord


class GateEvaluator:
    """Apply example-local acceptance rules to an already computed delta."""

    def __init__(self, config: GateConfig | None = None) -> None:
        self.config = config or GateConfig()

    def evaluate(
        self,
        *,
        delta: OptimizationDelta,
        optimization: OptimizationRunRecord | None = None,
    ) -> GateDecision:
        summary = self._summary(delta, optimization)
        rule_results = [
            self._validation_score_rule(delta),
            self._validation_pass_rate_rule(delta),
            self._new_failure_rule(delta),
            self._regression_rule(delta),
            self._critical_case_rule(delta),
            self._cost_rule(summary),
            self._overfit_rule(delta),
        ]
        accepted = all(result.passed for result in rule_results)
        return GateDecision(
            decision="accept" if accepted else "reject",
            accepted=accepted,
            reason=_decision_reason(accepted, rule_results),
            rule_results=rule_results,
            summary=summary,
            config=self.config,
            recommended_action="use_candidate_prompts" if accepted else "keep_baseline_prompts",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _summary(
        self,
        delta: OptimizationDelta,
        optimization: OptimizationRunRecord | None,
    ) -> dict[str, Any]:
        critical_regressions = _critical_regressions(delta.val.case_deltas, self.config.critical_case_ids)
        baseline_cost, candidate_cost = _costs(delta)
        cost_delta = _round(candidate_cost - baseline_cost)
        return {
            "baseline_val_score": delta.val.baseline_score,
            "candidate_val_score": delta.val.candidate_score,
            "val_score_delta": delta.val.score_delta,
            "val_pass_rate_delta": delta.val.pass_rate_delta,
            "train_score_delta": delta.train.score_delta,
            "new_fail_count": _count_change(delta.val.case_deltas, "new_fail"),
            "regression_count": sum(1 for case in delta.val.case_deltas if case.regression),
            "critical_regression_count": len(critical_regressions),
            "overfit_detected": _overfit_detected(delta, self.config),
            "baseline_cost": baseline_cost,
            "candidate_cost": candidate_cost,
            "cost_delta": cost_delta,
            "cost_ratio": _cost_ratio(baseline_cost, candidate_cost),
            "total_optimization_cost": optimization.total_cost if optimization else 0.0,
        }

    def _validation_score_rule(self, delta: OptimizationDelta) -> GateRuleResult:
        if not self.config.require_validation_improvement:
            return GateRuleResult(
                rule_name="validation_score_gain",
                passed=True,
                severity="required",
                message="Validation score improvement is not required by gate config.",
                evidence={"required": False},
            )
        passed = delta.val.score_delta >= self.config.min_val_score_gain
        return GateRuleResult(
            rule_name="validation_score_gain",
            passed=passed,
            severity="required",
            message=("Validation score gain satisfies the minimum threshold."
                     if passed else "Validation score gain is below the minimum threshold."),
            evidence={
                "score_delta": delta.val.score_delta,
                "min_val_score_gain": self.config.min_val_score_gain,
                "baseline_val_score": delta.val.baseline_score,
                "candidate_val_score": delta.val.candidate_score,
            },
        )

    def _validation_pass_rate_rule(self, delta: OptimizationDelta) -> GateRuleResult:
        passed = delta.val.pass_rate_delta >= self.config.min_val_pass_rate_gain
        return GateRuleResult(
            rule_name="validation_pass_rate_gain",
            passed=passed,
            severity="required",
            message=("Validation pass-rate gain satisfies the minimum threshold."
                     if passed else "Validation pass-rate gain is below the minimum threshold."),
            evidence={
                "pass_rate_delta": delta.val.pass_rate_delta,
                "min_val_pass_rate_gain": self.config.min_val_pass_rate_gain,
                "baseline_val_pass_rate": delta.val.baseline_pass_rate,
                "candidate_val_pass_rate": delta.val.candidate_pass_rate,
            },
        )

    def _new_failure_rule(self, delta: OptimizationDelta) -> GateRuleResult:
        new_fail_ids = [case.id for case in delta.val.case_deltas if case.change_type == "new_fail"]
        passed = self.config.allow_new_failures or not new_fail_ids
        return GateRuleResult(
            rule_name="new_failures",
            passed=passed,
            severity="required",
            message=("No new validation failures were introduced."
                     if passed else "Candidate introduced new validation failures."),
            evidence={
                "allow_new_failures": self.config.allow_new_failures,
                "new_fail_case_ids": new_fail_ids,
            },
        )

    def _regression_rule(self, delta: OptimizationDelta) -> GateRuleResult:
        regression_ids = [case.id for case in delta.val.case_deltas if case.regression]
        passed = self.config.allow_regressions or not regression_ids
        return GateRuleResult(
            rule_name="validation_regressions",
            passed=passed,
            severity="required",
            message=("No validation regressions were detected."
                     if passed else "Candidate introduced validation regressions."),
            evidence={
                "allow_regressions": self.config.allow_regressions,
                "regression_case_ids": regression_ids,
            },
        )

    def _critical_case_rule(self, delta: OptimizationDelta) -> GateRuleResult:
        critical_regressions = _critical_regressions(delta.val.case_deltas, self.config.critical_case_ids)
        passed = not critical_regressions
        return GateRuleResult(
            rule_name="critical_case_regressions",
            passed=passed,
            severity="required",
            message=("No critical validation cases regressed."
                     if passed else "One or more critical validation cases regressed."),
            evidence={
                "critical_case_ids": self.config.critical_case_ids,
                "critical_regression_case_ids": critical_regressions,
            },
        )

    def _cost_rule(self, summary: dict[str, Any]) -> GateRuleResult:
        cost_delta = float(summary["cost_delta"])
        cost_ratio = summary["cost_ratio"]
        delta_ok = cost_delta <= self.config.max_cost_delta
        ratio_ok = cost_ratio is None or cost_ratio <= self.config.max_cost_ratio
        passed = delta_ok and ratio_ok
        return GateRuleResult(
            rule_name="cost_budget",
            passed=passed,
            severity="required",
            message=("Candidate cost is within the configured budget."
                     if passed else "Candidate cost exceeds the configured budget."),
            evidence={
                "baseline_cost": summary["baseline_cost"],
                "candidate_cost": summary["candidate_cost"],
                "cost_delta": cost_delta,
                "cost_ratio": cost_ratio,
                "max_cost_delta": self.config.max_cost_delta,
                "max_cost_ratio": self.config.max_cost_ratio,
            },
        )

    def _overfit_rule(self, delta: OptimizationDelta) -> GateRuleResult:
        overfit = _overfit_detected(delta, self.config)
        return GateRuleResult(
            rule_name="overfit_detection",
            passed=not overfit,
            severity="required",
            message=("No train-only overfit pattern was detected."
                     if not overfit else "Candidate appears to improve train while degrading validation."),
            evidence={
                "enabled": bool(self.config.overfit_policy.get("enabled", True)),
                "train_score_delta": delta.train.score_delta,
                "val_score_delta": delta.val.score_delta,
                "train_gain_min": float(self.config.overfit_policy.get("train_gain_min", 0.05)),
                "val_drop_tolerance": float(self.config.overfit_policy.get("val_drop_tolerance", 0.0)),
            },
        )


def _decision_reason(accepted: bool, rule_results: list[GateRuleResult]) -> str:
    if accepted:
        return "Validation improved without gate-blocking regressions."
    failed = [result.message for result in rule_results if not result.passed]
    return " ".join(failed)


def _critical_regressions(case_deltas: list[CaseDelta], critical_case_ids: list[str]) -> list[str]:
    critical = set(critical_case_ids)
    if not critical:
        return []
    return [
        case.id for case in case_deltas if case.id in critical and (
            case.change_type in {"new_fail", "score_down", "missing_candidate"} or _has_metric_regression(case))
    ]


def _has_metric_regression(case: CaseDelta) -> bool:
    return any(metric.status_transition == "passed_to_failed" for metric in case.metric_deltas)


def _overfit_detected(delta: OptimizationDelta, config: GateConfig) -> bool:
    policy = config.overfit_policy
    if not bool(policy.get("enabled", True)):
        return False
    train_gain_min = float(policy.get("train_gain_min", 0.05))
    val_drop_tolerance = float(policy.get("val_drop_tolerance", 0.0))
    return delta.train.score_delta >= train_gain_min and delta.val.score_delta < -val_drop_tolerance


def _count_change(case_deltas: list[CaseDelta], change_type: str) -> int:
    return sum(1 for case in case_deltas if case.change_type == change_type)


def _costs(delta: OptimizationDelta) -> tuple[float, float]:
    cases = delta.train.case_deltas + delta.val.case_deltas
    baseline_cost = sum(_cost(case.baseline) for case in cases)
    candidate_cost = sum(_cost(case.candidate) for case in cases)
    return _round(baseline_cost), _round(candidate_cost)


def _cost(reference: dict[str, Any]) -> float:
    value = reference.get("cost")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _cost_ratio(baseline_cost: float, candidate_cost: float) -> float | None:
    if baseline_cost == 0:
        return None
    return _round(candidate_cost / baseline_cost)


def _round(value: float) -> float:
    return round(value, 6)
