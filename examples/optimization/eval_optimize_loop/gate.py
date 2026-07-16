# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Deterministic acceptance Gate for stage 3b."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from .config import BudgetConfig
from .config import GateConfig
from .schemas import CaseDiff
from .schemas import CaseEvaluation
from .schemas import EvaluationAnalysis
from .schemas import GateDecision
from .schemas import GateRuleId
from .schemas import GateRuleResult
from .schemas import ObservableValue
from .schemas import ResourceMeasurements


class GateEvaluationError(ValueError):
    """Stage 3a analysis is structurally unsafe for Gate evaluation."""


def _case_ids(
    cases: Sequence[CaseEvaluation | CaseDiff],
    *,
    context: str,
) -> set[str]:
    ids = [case.eval_id for case in cases]
    if len(ids) != len(set(ids)):
        raise GateEvaluationError(f"{context} contains duplicate case ids")
    return set(ids)


def _validate_analysis(analysis: EvaluationAnalysis) -> None:
    if analysis.train_diff.split != "train":
        raise GateEvaluationError("train_diff.split must be 'train'")
    if analysis.validation_diff.split != "validation":
        raise GateEvaluationError("validation_diff.split must be 'validation'")

    evaluations = (
        ("baseline_train", analysis.baseline_train, "baseline", "train"),
        ("baseline_validation", analysis.baseline_validation, "baseline", "validation"),
        ("candidate_train", analysis.candidate_train, "candidate", "train"),
        ("candidate_validation", analysis.candidate_validation, "candidate", "validation"),
    )
    for label, evaluation, expected_phase, expected_split in evaluations:
        if evaluation.phase != expected_phase or evaluation.split != expected_split:
            raise GateEvaluationError(f"{label} has an unexpected phase or split")
        _case_ids(evaluation.cases, context=label)
        for case in evaluation.cases:
            metric_names = [metric.metric_name for metric in case.metrics]
            if len(metric_names) != len(set(metric_names)):
                raise GateEvaluationError(
                    f"{label} case {case.eval_id!r} contains duplicate metric names"
                )

    train_diff_ids = _case_ids(analysis.train_diff.cases, context="train_diff")
    validation_diff_ids = _case_ids(
        analysis.validation_diff.cases,
        context="validation_diff",
    )
    candidate_train_ids = {case.eval_id for case in analysis.candidate_train.cases}
    candidate_validation_ids = {
        case.eval_id for case in analysis.candidate_validation.cases
    }
    if train_diff_ids != candidate_train_ids:
        raise GateEvaluationError("train diff and candidate evaluation case ids do not match")
    if validation_diff_ids != candidate_validation_ids:
        raise GateEvaluationError(
            "validation diff and candidate evaluation case ids do not match"
        )


def _evaluation_completeness(analysis: EvaluationAnalysis) -> GateRuleResult:
    incomplete_case_ids: set[str] = set()
    incomplete_metric_names: set[str] = set()
    evaluations = (
        analysis.baseline_train,
        analysis.baseline_validation,
        analysis.candidate_train,
        analysis.candidate_validation,
    )
    complete = True
    for evaluation in evaluations:
        if not evaluation.cases or evaluation.average_score.status != "available":
            complete = False
        for case in evaluation.cases:
            if (
                case.status == "not_evaluated"
                or case.average_score.status != "available"
                or not case.metrics
            ):
                complete = False
                incomplete_case_ids.add(case.eval_id)
            for metric in case.metrics:
                if metric.status == "not_evaluated" or metric.score.status != "available":
                    complete = False
                    incomplete_case_ids.add(case.eval_id)
                    incomplete_metric_names.add(metric.metric_name)
    return GateRuleResult(
        rule_id="evaluation_completeness",
        outcome="pass" if complete else "reject",
        message=(
            "All four evaluations contain complete case and metric results."
            if complete
            else "One or more evaluation cases or metrics are incomplete."
        ),
        case_ids=sorted(incomplete_case_ids),
        metric_names=sorted(incomplete_metric_names),
    )


def _minimum_validation_score_delta(
    analysis: EvaluationAnalysis,
    config: GateConfig,
) -> GateRuleResult:
    delta = analysis.validation_diff.score_delta
    passed = (
        delta.status == "available"
        and float(delta.value) >= config.min_validation_score_delta
    )
    return GateRuleResult(
        rule_id="minimum_validation_score_delta",
        outcome="pass" if passed else "reject",
        message=(
            "Validation score improvement meets the configured minimum."
            if passed
            else "Validation score improvement is unavailable or below the configured minimum."
        ),
        observed={"validation_score_delta": delta},
        threshold=config.min_validation_score_delta,
    )


def _validation_pass_rate(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    if not config.reject_on_validation_pass_rate_drop:
        return GateRuleResult(
            rule_id="validation_pass_rate_non_decrease",
            outcome="skipped",
            message="Validation pass-rate protection is disabled.",
        )
    baseline_total = len(analysis.baseline_validation.cases)
    candidate_total = len(analysis.candidate_validation.cases)
    if baseline_total == 0 or candidate_total == 0:
        return GateRuleResult(
            rule_id="validation_pass_rate_non_decrease",
            outcome="reject",
            message="Validation pass rate is unavailable because an evaluation has no cases.",
        )
    baseline_rate = analysis.baseline_validation.passed_case_count / baseline_total
    candidate_rate = analysis.candidate_validation.passed_case_count / candidate_total
    passed = candidate_rate >= baseline_rate
    return GateRuleResult(
        rule_id="validation_pass_rate_non_decrease",
        outcome="pass" if passed else "reject",
        message=(
            "Validation pass rate did not decrease."
            if passed
            else "Validation pass rate decreased from baseline."
        ),
        observed={
            "baseline_validation_pass_rate": ObservableValue(
                status="available", value=baseline_rate, unit="ratio"
            ),
            "candidate_validation_pass_rate": ObservableValue(
                status="available", value=candidate_rate, unit="ratio"
            ),
        },
    )


def _all_case_diffs(analysis: EvaluationAnalysis) -> list[CaseDiff]:
    return sorted(
        [*analysis.train_diff.cases, *analysis.validation_diff.cases],
        key=lambda case: (case.split, case.eval_id),
    )


def _new_hard_failures(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    if not config.reject_new_hard_fail:
        return GateRuleResult(
            rule_id="no_new_hard_fail",
            outcome="skipped",
            message="New hard-failure protection is disabled.",
        )
    case_ids = sorted(
        case.eval_id
        for case in _all_case_diffs(analysis)
        if case.is_hard and case.change == "newly_failed"
    )
    return GateRuleResult(
        rule_id="no_new_hard_fail",
        outcome="reject" if case_ids else "pass",
        message=(
            "New hard failures were found."
            if case_ids
            else "No new hard failures were found."
        ),
        case_ids=case_ids,
    )


def _critical_regressions(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    if not config.reject_critical_regression:
        return GateRuleResult(
            rule_id="no_critical_regression",
            outcome="skipped",
            message="Critical-case regression protection is disabled.",
        )
    case_ids = sorted(
        case.eval_id
        for case in _all_case_diffs(analysis)
        if case.is_critical and case.change in {"newly_failed", "regressed"}
    )
    return GateRuleResult(
        rule_id="no_critical_regression",
        outcome="reject" if case_ids else "pass",
        message=(
            "Critical-case regressions were found."
            if case_ids
            else "No critical-case regressions were found."
        ),
        case_ids=case_ids,
    )


def _severe_regressions(analysis: EvaluationAnalysis) -> GateRuleResult:
    case_ids = sorted(
        case.eval_id for case in _all_case_diffs(analysis) if case.severe_regression
    )
    return GateRuleResult(
        rule_id="no_severe_regression",
        outcome="reject" if case_ids else "pass",
        message=(
            "Severe case regressions were found."
            if case_ids
            else "No severe case regressions were found."
        ),
        case_ids=case_ids,
    )


def _required_metrics(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    failed_case_ids: set[str] = set()
    failed_metric_names: set[str] = set()
    for evaluation in (analysis.candidate_train, analysis.candidate_validation):
        for case in evaluation.cases:
            metric_map = {metric.metric_name: metric for metric in case.metrics}
            if config.required_metrics == "all":
                required_names = sorted(metric_map)
                if not required_names:
                    failed_case_ids.add(case.eval_id)
                    continue
            else:
                required_names = sorted(config.required_metrics)
            for name in required_names:
                metric = metric_map.get(name)
                if (
                    metric is None
                    or metric.status != "passed"
                    or metric.score.status != "available"
                ):
                    failed_case_ids.add(case.eval_id)
                    failed_metric_names.add(name)
    return GateRuleResult(
        rule_id="required_metrics",
        outcome="reject" if failed_case_ids else "pass",
        message=(
            "Required metrics are missing, unavailable, or below threshold."
            if failed_case_ids
            else "All required candidate metrics are available and passed."
        ),
        case_ids=sorted(failed_case_ids),
        metric_names=sorted(failed_metric_names),
    )


def _overfitting(analysis: EvaluationAnalysis) -> GateRuleResult:
    passed = analysis.overfit_status == "not_detected"
    return GateRuleResult(
        rule_id="no_overfitting",
        outcome="pass" if passed else "reject",
        message=(
            "No train-improvement/validation-regression pattern was detected."
            if passed
            else f"Overfit status is {analysis.overfit_status!r}: {analysis.overfit_reason}"
        ),
    )


def _budget_result(
    rule_id: GateRuleId,
    measurement_name: str,
    measurement: ObservableValue,
    limit: float | int | None,
    on_unavailable: Literal["reject", "warning"],
) -> GateRuleResult:
    if limit is None:
        return GateRuleResult(
            rule_id=rule_id,
            outcome="skipped",
            message=f"{measurement_name} budget is not configured.",
        )
    if measurement.status != "available":
        return GateRuleResult(
            rule_id=rule_id,
            outcome=on_unavailable,
            message=(
                f"{measurement_name} is unavailable; policy is {on_unavailable}."
            ),
            observed={measurement_name: measurement},
            threshold=float(limit),
        )
    passed = float(measurement.value) <= float(limit)
    return GateRuleResult(
        rule_id=rule_id,
        outcome="pass" if passed else "reject",
        message=(
            f"{measurement_name} is within the configured budget."
            if passed
            else f"{measurement_name} exceeds the configured budget."
        ),
        observed={measurement_name: measurement},
        threshold=float(limit),
    )


def evaluate_gate(
    analysis: EvaluationAnalysis,
    gate_config: GateConfig,
    budget_config: BudgetConfig,
    measurements: ResourceMeasurements,
) -> GateDecision:
    """Evaluate every configured rule and return one complete decision."""
    _validate_analysis(analysis)
    quality_results = [
        _evaluation_completeness(analysis),
        _minimum_validation_score_delta(analysis, gate_config),
        _validation_pass_rate(analysis, gate_config),
        _new_hard_failures(analysis, gate_config),
        _critical_regressions(analysis, gate_config),
        _severe_regressions(analysis),
        _required_metrics(analysis, gate_config),
        _overfitting(analysis),
    ]
    results = quality_results + [
        _budget_result(
            "cost_budget",
            "cost_usd",
            measurements.cost_usd,
            budget_config.max_cost_usd,
            budget_config.on_unavailable,
        ),
        _budget_result(
            "token_budget",
            "total_tokens",
            measurements.total_tokens,
            budget_config.max_tokens,
            budget_config.on_unavailable,
        ),
        _budget_result(
            "duration_budget",
            "duration_seconds",
            measurements.duration_seconds,
            budget_config.max_duration_seconds,
            budget_config.on_unavailable,
        ),
    ]
    rejection_reasons = [result.message for result in results if result.outcome == "reject"]
    warnings = [result.message for result in results if result.outcome == "warning"]
    return GateDecision(
        decision="reject" if rejection_reasons else "accept",
        rule_results=results,
        rejection_reasons=rejection_reasons,
        warnings=warnings,
    )
