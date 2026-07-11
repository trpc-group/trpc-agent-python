from __future__ import annotations

from math import inf
from typing import Iterable

from .models import CandidateReport, CaseDelta, GateDecision, GateRuleResult, GateSettings, SplitReport


def _rule(rule: str, passed: bool, actual: object, expected: object, reason: str) -> GateRuleResult:
    return GateRuleResult(rule=rule, passed=passed, actual=actual, expected=expected, reason=reason)


def _complete(baseline: SplitReport | None, candidate: SplitReport | None) -> bool:
    if baseline is None or candidate is None or not baseline.cases or not candidate.cases:
        return False
    baseline_ids = [case.eval_id for case in baseline.cases]
    candidate_ids = [case.eval_id for case in candidate.cases]
    return len(baseline_ids) == len(set(baseline_ids)) and len(candidate_ids) == len(set(candidate_ids)) and set(baseline_ids) == set(candidate_ids)


def evaluate_gate(
    baseline: SplitReport | None,
    candidate: SplitReport | None,
    *,
    settings: GateSettings,
    case_deltas: list[CaseDelta],
    train_score_delta: float | None = None,
    metric_floors: dict[str, float] | None = None,
    generation_cost_usd: float | None = None,
    duration_seconds: float | None = None,
    epsilon: float = 0.000001,
) -> GateDecision:
    """Apply only independently measured train/validation evidence to a proposal."""
    complete = _complete(baseline, candidate)
    score_delta = candidate.aggregate_score - baseline.aggregate_score if complete and baseline and candidate else None
    pass_rate_delta = candidate.pass_rate - baseline.pass_rate if complete and baseline and candidate else None
    new_hard_fails = sum(delta.hard_fail_added for delta in case_deltas)
    regressions = sum(delta.transition == "REGRESSION" for delta in case_deltas)
    critical_regression = any(delta.critical and delta.transition == "REGRESSION" for delta in case_deltas)
    overfit = bool(
        settings.reject_when_train_improves_but_validation_declines
        and train_score_delta is not None
        and train_score_delta > epsilon
        and (score_delta is None or score_delta < -epsilon)
    )
    generalization_gap = train_score_delta - score_delta if train_score_delta is not None and score_delta is not None else None
    rules = [
        _rule("evaluation_complete", complete, None if not complete else "complete", "complete", "independent validation evaluation is incomplete"),
        _rule("validation_score_delta_available", score_delta is not None, score_delta, "number", "validation aggregate score delta is required"),
        _rule("validation_pass_rate_delta_available", pass_rate_delta is not None, pass_rate_delta, "number", "validation pass-rate delta is required"),
        _rule("validation_score_improved", score_delta is not None and score_delta >= settings.min_validation_score_delta, score_delta, settings.min_validation_score_delta, "validation aggregate score must improve"),
        _rule("validation_pass_rate_not_worse", pass_rate_delta is not None and pass_rate_delta >= settings.min_validation_pass_rate_delta, pass_rate_delta, settings.min_validation_pass_rate_delta, "validation pass rate must not decline"),
        _rule("new_hard_fails", new_hard_fails <= settings.max_new_hard_fails, new_hard_fails, settings.max_new_hard_fails, "new hard failures are not allowed"),
        _rule("validation_regressions", regressions <= settings.max_validation_regressions, regressions, settings.max_validation_regressions, "validation regressions exceed the limit"),
        _rule("no_critical_regression", settings.allow_critical_case_regression or not critical_regression, critical_regression, False, "critical validation cases must not regress"),
        _rule("no_overfit", not overfit, overfit, False, "train improvement with validation decline is rejected"),
    ]
    for metric_name, floor in sorted((metric_floors or {}).items()):
        observed = min((case.metric_scores.get(metric_name, float("-inf")) for case in candidate.cases), default=float("-inf")) if candidate else float("-inf")
        rules.append(_rule(f"metric_floor:{metric_name}", observed >= floor, observed, floor, f"metric {metric_name} is below its validation floor"))
    if settings.max_generalization_gap is not None:
        rules.append(_rule("generalization_gap", generalization_gap is not None and generalization_gap <= settings.max_generalization_gap, generalization_gap, settings.max_generalization_gap, "train/validation generalization gap exceeds the limit"))
    if settings.max_generation_cost_usd is not None:
        rules.append(_rule("generation_cost_budget", generation_cost_usd is not None and generation_cost_usd <= settings.max_generation_cost_usd, generation_cost_usd, settings.max_generation_cost_usd, "generation cost exceeds the budget"))
    if settings.max_duration_seconds is not None:
        rules.append(_rule("duration_budget", duration_seconds is not None and duration_seconds <= settings.max_duration_seconds, duration_seconds, settings.max_duration_seconds, "generation duration exceeds the budget"))
    tied = score_delta is not None and pass_rate_delta is not None and abs(score_delta) <= epsilon and abs(pass_rate_delta) <= epsilon
    rules.append(_rule("tie_policy", not (settings.tie_policy == "reject" and tied), tied, False, "tie policy rejects a non-improving validation outcome"))
    warnings = ["generation cost is unknown"] if generation_cost_usd is None else []
    failed = [rule.reason for rule in rules if not rule.passed]
    risk_level = "high" if critical_regression or overfit else ("medium" if failed else "low")
    return GateDecision(accepted=not failed, risk_level=risk_level, rules=rules, reasons=failed or ["candidate passed all independent gate rules"], warnings=warnings)


def select_winner(candidates: Iterable[CandidateReport]) -> str | None:
    """Choose only Gate-approved, independently evaluated records with a stable key."""
    eligible = [candidate for candidate in candidates if candidate.accepted and candidate.independently_evaluated and candidate.train and candidate.validation]
    if not eligible:
        return None

    def rank(candidate: CandidateReport) -> tuple[float, ...] | tuple[float, float, float, float, float, float, str]:
        gate = candidate.gate
        rules = gate.rules if gate else []
        failures = {rule.rule: rule.actual for rule in rules}
        hard_fails = float(failures.get("new_hard_fails", 0) or 0)
        critical = 1.0 if failures.get("no_critical_regression") is True else 0.0
        return (
            hard_fails,
            critical,
            -candidate.validation.pass_rate,
            -candidate.validation.aggregate_score,
            candidate.generation_cost_usd if candidate.generation_cost_usd is not None else inf,
            candidate.duration_seconds if candidate.duration_seconds is not None else inf,
            candidate.candidate_id,
        )

    return min(eligible, key=rank).candidate_id
