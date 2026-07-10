from __future__ import annotations

from .models import CaseDelta, GateDecision, GateRuleResult, GateSettings, SplitReport


def evaluate_gate(
    baseline: SplitReport,
    candidate: SplitReport,
    *,
    settings: GateSettings,
    case_deltas: list[CaseDelta],
    train_score_delta: float | None = None,
) -> GateDecision:
    score_delta = candidate.aggregate_score - baseline.aggregate_score
    pass_rate_delta = candidate.pass_rate - baseline.pass_rate
    new_hard_fails = sum(delta.hard_fail_added for delta in case_deltas)
    regressions = sum(delta.transition == "REGRESSION" for delta in case_deltas)
    critical_regression = any(delta.critical and delta.transition == "REGRESSION" for delta in case_deltas)
    overfit = bool(settings.reject_when_train_improves_but_validation_declines and train_score_delta is not None and train_score_delta > 0 and score_delta < 0)
    rules = [
        GateRuleResult(rule="validation_score_improved", passed=score_delta >= settings.min_validation_score_delta, actual=score_delta, expected=settings.min_validation_score_delta, reason="validation aggregate score must improve"),
        GateRuleResult(rule="validation_pass_rate_not_worse", passed=pass_rate_delta >= settings.min_validation_pass_rate_delta, actual=pass_rate_delta, expected=settings.min_validation_pass_rate_delta, reason="validation pass rate must not decline"),
        GateRuleResult(rule="new_hard_fails", passed=new_hard_fails <= settings.max_new_hard_fails, actual=new_hard_fails, expected=settings.max_new_hard_fails, reason="new hard failures are not allowed"),
        GateRuleResult(rule="validation_regressions", passed=regressions <= settings.max_validation_regressions, actual=regressions, expected=settings.max_validation_regressions, reason="validation regressions exceed the limit"),
        GateRuleResult(rule="no_critical_regression", passed=settings.allow_critical_case_regression or not critical_regression, actual=critical_regression, expected=False, reason="critical validation cases must not regress"),
        GateRuleResult(rule="no_overfit", passed=not overfit, actual=overfit, expected=False, reason="train improvement with validation decline is rejected"),
    ]
    failed = [rule.reason for rule in rules if not rule.passed]
    return GateDecision(accepted=not failed, risk_level="high" if critical_regression or overfit else ("medium" if failed else "low"), rules=rules, reasons=failed or ["candidate passed all independent gate rules"])
