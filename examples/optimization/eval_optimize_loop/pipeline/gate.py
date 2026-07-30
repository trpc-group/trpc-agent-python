"""Pure deterministic acceptance gate with fixed AND semantics."""

from __future__ import annotations

import math
from typing import Any

from .configuration import GateConfig
from .models import (
    CandidateProposal,
    ComparisonSnapshot,
    CostSummary,
    Decision,
    GateCheck,
    GateDecision,
)


def _check(code: str, passed: bool, observed: Any, threshold: Any, message: str) -> GateCheck:
    return GateCheck(code=code, passed=passed, observed=observed, threshold=threshold, message=message)


def _invalid_input(message: str) -> GateDecision:
    return GateDecision(
        decision=Decision.ERROR,
        accepted=False,
        checks=(_check("REPORT_COMPLETE", False, "invalid", "complete", message), ),
        reasons=("GATE_INPUT_INVALID", ),
    )


def evaluate_gate(
    *,
    train: ComparisonSnapshot,
    validation: ComparisonSnapshot,
    candidate: CandidateProposal,
    cost: CostSummary,
    duration_seconds: float,
    baseline_prompt_hashes: dict[str, str],
    candidate_prompt_hashes: dict[str, str],
    config: GateConfig,
    report_complete: bool = True,
) -> GateDecision:
    """Evaluate all ten checks in their stable order."""

    try:
        if train.split.value != "train" or validation.split.value != "validation":
            raise ValueError("comparison splits are misplaced")
        if set(baseline_prompt_hashes) != set(candidate_prompt_hashes) or not baseline_prompt_hashes:
            raise ValueError("prompt hash schemas are incomplete")
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("duration must be finite and non-negative")
        for value in (
                train.score_delta,
                train.pass_rate_delta,
                validation.score_delta,
                validation.pass_rate_delta,
                *train.metric_deltas.values(),
                *validation.metric_deltas.values(),
        ):
            if not math.isfinite(value):
                raise ValueError("gate inputs must be finite")
        missing_metrics = set(config.metric_max_regression) - set(validation.metric_deltas)
        if missing_metrics:
            raise ValueError(f"configured metrics are missing: {sorted(missing_metrics)}")
    except (AttributeError, TypeError, ValueError) as error:
        return _invalid_input(str(error))

    checks: list[GateCheck] = []
    failed_reasons: list[str] = []

    def add(
        code: str,
        passed: bool,
        observed: Any,
        threshold: Any,
        message: str,
        reason: str,
    ) -> None:
        checks.append(_check(code, passed, observed, threshold, message))
        if not passed:
            failed_reasons.append(reason)

    add(
        "REPORT_COMPLETE",
        report_complete,
        report_complete,
        True,
        "All required regression snapshots and report inputs are present.",
        "REPORT_INCOMPLETE",
    )
    hashes_changed = baseline_prompt_hashes != candidate_prompt_hashes
    add(
        "CANDIDATE_CHANGED",
        candidate.changed and hashes_changed,
        hashes_changed,
        True,
        "Candidate prompt hashes differ from baseline.",
        "CANDIDATE_UNCHANGED",
    )
    score_passed = validation.score_delta + config.epsilon >= config.min_validation_score_delta
    add(
        "VALIDATION_SCORE_DELTA",
        score_passed,
        validation.score_delta,
        config.min_validation_score_delta,
        "Held-out validation score delta meets the configured minimum.",
        "VALIDATION_SCORE_DELTA_BELOW_MINIMUM",
    )
    pass_rate_passed = validation.pass_rate_delta + config.epsilon >= config.min_validation_pass_rate_delta
    add(
        "VALIDATION_PASS_RATE_DELTA",
        pass_rate_passed,
        validation.pass_rate_delta,
        config.min_validation_pass_rate_delta,
        "Held-out validation pass-rate delta meets the configured minimum.",
        "VALIDATION_PASS_RATE_DELTA_BELOW_MINIMUM",
    )
    new_hard_failures = sum(case.new_hard_failure for case in validation.cases)
    add(
        "NO_NEW_HARD_FAIL",
        new_hard_failures <= config.max_new_hard_failures,
        new_hard_failures,
        config.max_new_hard_failures,
        "New held-out hard failures stay within budget.",
        "NEW_HARD_FAIL_BUDGET_EXCEEDED",
    )

    def is_critical_regression(case) -> bool:
        new_failure = case.baseline_passed and not case.candidate_passed
        below_delta = case.delta + config.epsilon < config.critical_case_min_delta
        return case.critical and (new_failure or below_delta)

    critical_regressions = [case.case_id for case in validation.cases if is_critical_regression(case)]
    add(
        "CRITICAL_CASE_NON_REGRESSION",
        not critical_regressions,
        critical_regressions,
        config.critical_case_min_delta,
        "Critical held-out cases do not regress.",
        "CRITICAL_CASE_REGRESSION",
    )
    metric_regressions = {
        metric: validation.metric_deltas[metric]
        for metric, limit in config.metric_max_regression.items()
        if validation.metric_deltas[metric] + config.epsilon < -limit
    }
    add(
        "METRIC_NON_REGRESSION",
        not metric_regressions,
        metric_regressions,
        config.metric_max_regression,
        "Configured validation metrics stay within regression limits.",
        "METRIC_REGRESSION",
    )
    if config.max_cost_usd is None:
        cost_passed, cost_observed, cost_reason = True, cost.total_cost_usd, "COST_BUDGET_EXCEEDED"
    elif cost.total_cost_usd is None or any(source.cost_usd is None for source in cost.sources):
        cost_passed, cost_observed, cost_reason = False, None, "COST_UNAVAILABLE"
    else:
        cost_passed = cost.total_cost_usd <= config.max_cost_usd + config.epsilon
        cost_observed, cost_reason = cost.total_cost_usd, "COST_BUDGET_EXCEEDED"
    add(
        "COST_BUDGET",
        cost_passed,
        cost_observed,
        config.max_cost_usd,
        "Known enabled costs stay within budget.",
        cost_reason,
    )
    duration_passed = (config.max_duration_seconds is None
                       or duration_seconds <= config.max_duration_seconds + config.epsilon)
    add(
        "DURATION_BUDGET",
        duration_passed,
        duration_seconds,
        config.max_duration_seconds,
        "Gate-observed duration stays within budget.",
        "DURATION_BUDGET_EXCEEDED",
    )
    overfit = config.overfit_guard and train.score_delta > config.epsilon and not (score_passed and pass_rate_passed)
    add(
        "OVERFIT_GUARD",
        not overfit,
        {
            "trainScoreDelta": train.score_delta,
            "validationScoreDelta": validation.score_delta
        },
        "train improvement requires held-out validation thresholds",
        "Train-only improvement is rejected as overfitting.",
        "OVERFIT_TRAIN_UP_VALIDATION_DOWN",
    )
    accepted = all(check.passed for check in checks)
    return GateDecision(
        decision=Decision.ACCEPT if accepted else Decision.REJECT,
        accepted=accepted,
        checks=tuple(checks),
        reasons=tuple(dict.fromkeys(failed_reasons)),
    )
