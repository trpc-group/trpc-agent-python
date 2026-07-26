"""Failure attribution, case deltas, and acceptance gate logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Attribution
from .models import CaseDelta
from .models import CaseSnapshot
from .models import ChangeKind
from .models import CostSummary
from .models import EvaluationSnapshot
from .models import FailureCategory
from .models import GateCheck
from .models import GateConfig
from .models import GateDecision
from .models import SCORE_EPSILON
from .models import SplitDelta

FORMAT_MARKERS = ("format", "json", "schema", "parse")
RUBRIC_MARKERS = ("rubric", "judge", "quality")
KNOWLEDGE_MARKERS = ("knowledge", "recall", "retriev")


def attribute_cases(snapshot: EvaluationSnapshot) -> tuple[list[Attribution], dict[FailureCategory, int]]:
    """Attribute all failed cases with one deterministic primary rule."""
    attributions = [attribute_case(case, snapshot.primary_metric) for case in snapshot.cases if not case.passed]
    counts: dict[FailureCategory, int] = {}
    for attribution in attributions:
        counts[attribution.category] = counts.get(attribution.category, 0) + 1
    return attributions, counts


def attribute_case(case: CaseSnapshot, primary_metric: str) -> Attribution:
    """Return an explainable category and evidence for one failed case."""
    if case.hard_failure or case.error_message:
        return Attribution(
            case_id=case.case_id,
            category=FailureCategory.EXECUTION,
            rule_id="execution.error",
            evidence=case.error_message or "hard failure",
        )
    if _has_tool_mismatch(case):
        return Attribution(
            case_id=case.case_id,
            category=FailureCategory.TOOL_CALL,
            rule_id="trajectory.tool_name",
            evidence="actual and expected tool names differ",
        )
    if _has_argument_mismatch(case):
        return Attribution(
            case_id=case.case_id,
            category=FailureCategory.TOOL_ARGUMENT,
            rule_id="trajectory.arguments",
            evidence="tool names match but arguments differ",
        )
    reason_text = " ".join(case.reasons.values()).lower()
    if _contains_any(reason_text, RUBRIC_MARKERS):
        return Attribution(case.case_id, FailureCategory.RUBRIC, "metric.rubric", reason_text)
    if _contains_any(reason_text, KNOWLEDGE_MARKERS):
        return Attribution(case.case_id, FailureCategory.KNOWLEDGE, "metric.knowledge", reason_text)
    if _contains_any(reason_text, FORMAT_MARKERS):
        return Attribution(case.case_id, FailureCategory.FORMAT, "response.format", reason_text)
    if case.metric_statuses.get(primary_metric) == "FAILED":
        return Attribution(
            case_id=case.case_id,
            category=FailureCategory.RESPONSE,
            rule_id="response.mismatch",
            evidence="final response metric did not meet threshold",
        )
    return Attribution(
        case_id=case.case_id,
        category=FailureCategory.OTHER,
        rule_id="fallback.other",
        evidence="no specific rule matched",
    )


def compare_snapshots(
    baseline: EvaluationSnapshot,
    candidate: EvaluationSnapshot,
) -> SplitDelta:
    """Compare baseline and candidate case outcomes for one split."""
    baseline_cases = {case.case_id: case for case in baseline.cases}
    candidate_cases = {case.case_id: case for case in candidate.cases}
    case_ids = sorted(set(baseline_cases) | set(candidate_cases))
    deltas = [
        _case_delta(case_id, baseline_cases.get(case_id), candidate_cases.get(case_id), baseline.primary_metric)
        for case_id in case_ids
    ]
    return SplitDelta(
        split=baseline.split,
        baseline_score=baseline.primary_score,
        candidate_score=candidate.primary_score,
        score_delta=_score_delta(baseline.primary_score, candidate.primary_score),
        cases=deltas,
    )


@dataclass(frozen=True)
class GateInput:
    """Inputs for one gate evaluation."""

    config: GateConfig
    train_delta: SplitDelta
    validation_delta: SplitDelta
    baseline_validation: EvaluationSnapshot
    candidate_validation: EvaluationSnapshot
    cost: CostSummary
    duration_seconds: float


def evaluate_gate(inputs: GateInput) -> GateDecision:
    """Apply all configured acceptance predicates with fail-closed semantics."""
    checks = [
        _score_check(inputs.validation_delta, inputs.config),
        _regression_check(inputs.validation_delta),
        _hard_failure_check(inputs),
        _critical_check(inputs),
        _cost_check(inputs.cost, inputs.config),
        _duration_check(inputs.duration_seconds, inputs.config),
    ]
    overfitting = _is_overfitting(inputs.train_delta, inputs.validation_delta, inputs.config)
    checks.append(
        GateCheck(
            name="overfitting",
            passed=not overfitting,
            reason="train improved while validation regressed" if overfitting else "no overfitting signal",
        ))
    reasons = [check.reason for check in checks if not check.passed]
    return GateDecision(accepted=not reasons, overfitting=overfitting, checks=checks, reasons=reasons)


def _case_delta(
    case_id: str,
    baseline: CaseSnapshot | None,
    candidate: CaseSnapshot | None,
    primary_metric: str,
) -> CaseDelta:
    baseline_score = baseline.metric_scores.get(primary_metric) if baseline else None
    candidate_score = candidate.metric_scores.get(primary_metric) if candidate else None
    baseline_passed = baseline.passed if baseline else False
    candidate_passed = candidate.passed if candidate else False
    change = _change_kind(baseline_passed, candidate_passed, baseline_score, candidate_score)
    return CaseDelta(
        case_id=case_id,
        baseline_passed=baseline_passed,
        candidate_passed=candidate_passed,
        baseline_score=baseline_score,
        candidate_score=candidate_score,
        score_delta=_score_delta(baseline_score, candidate_score),
        change=change,
        hard_failure_added=bool(candidate is None
                                or (candidate.hard_failure and not (baseline and baseline.hard_failure))),
    )


def _change_kind(
    baseline_passed: bool,
    candidate_passed: bool,
    baseline_score: float | None,
    candidate_score: float | None,
) -> ChangeKind:
    if not baseline_passed and candidate_passed:
        return ChangeKind.NEW_PASS
    if baseline_passed and not candidate_passed:
        return ChangeKind.NEW_FAIL
    delta = _score_delta(baseline_score, candidate_score)
    if delta is None or abs(delta) <= SCORE_EPSILON:
        return ChangeKind.UNCHANGED
    return ChangeKind.IMPROVED if delta > 0 else ChangeKind.REGRESSED


def _score_delta(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None:
        return None
    return candidate - baseline


def _score_check(delta: SplitDelta, config: GateConfig) -> GateCheck:
    value = delta.score_delta
    passed = value is not None and value + SCORE_EPSILON >= config.min_score_delta
    reason = f"validation delta={value!r}, required>={config.min_score_delta}"
    return GateCheck(name="validation_score", passed=passed, reason=reason)


def _regression_check(delta: SplitDelta) -> GateCheck:
    value = delta.score_delta
    passed = value is not None and value >= -SCORE_EPSILON
    reason = "validation did not regress" if passed else f"validation regression={value!r}"
    return GateCheck(name="validation_regression", passed=passed, reason=reason)


def _hard_failure_check(inputs: GateInput) -> GateCheck:
    added = [case.case_id for case in inputs.validation_delta.cases if case.hard_failure_added]
    candidate_cases = {case.case_id: case for case in inputs.candidate_validation.cases}
    configured = [
        case_id for case_id in inputs.config.hard_case_ids
        if case_id not in candidate_cases or not candidate_cases[case_id].passed
    ]
    metric_failures = [
        case.case_id for case in inputs.candidate_validation.cases if any(
            case.metric_statuses.get(metric) != "PASSED" for metric in inputs.config.hard_metric_names)
    ]
    failures = sorted(set(added + configured + metric_failures))
    return GateCheck(
        name="hard_failures",
        passed=not failures,
        reason="no hard failures" if not failures else f"hard failures: {failures}",
    )


def _critical_check(inputs: GateInput) -> GateCheck:
    baseline = {case.case_id: case for case in inputs.baseline_validation.cases}
    candidate = {case.case_id: case for case in inputs.candidate_validation.cases}
    failures = []
    for case_id in inputs.config.critical_case_ids:
        before = baseline.get(case_id)
        after = candidate.get(case_id)
        if before is None or after is None:
            failures.append(case_id)
            continue
        score_delta = _score_delta(
            before.metric_scores.get(inputs.config.primary_metric),
            after.metric_scores.get(inputs.config.primary_metric),
        )
        if not after.passed or (score_delta is not None and score_delta < -inputs.config.max_critical_regression):
            failures.append(case_id)
    return GateCheck(
        name="critical_cases",
        passed=not failures,
        reason="critical cases preserved" if not failures else f"critical regressions: {failures}",
    )


def _cost_check(cost: CostSummary, config: GateConfig) -> GateCheck:
    if config.max_total_cost is None:
        return GateCheck(name="cost", passed=True, reason="cost budget disabled")
    passed = cost.cost_complete and cost.total_cost <= config.max_total_cost + SCORE_EPSILON
    reason = f"cost={cost.total_cost}, complete={cost.cost_complete}, limit={config.max_total_cost}"
    return GateCheck(name="cost", passed=passed, reason=reason)


def _duration_check(duration: float, config: GateConfig) -> GateCheck:
    if config.max_duration_seconds is None:
        return GateCheck(name="duration", passed=True, reason="duration budget disabled")
    passed = duration <= config.max_duration_seconds
    return GateCheck(
        name="duration",
        passed=passed,
        reason=f"duration={duration:.3f}s, limit={config.max_duration_seconds}s",
    )


def _is_overfitting(train: SplitDelta, validation: SplitDelta, config: GateConfig) -> bool:
    train_delta = train.score_delta
    validation_delta = validation.score_delta
    return (train_delta is not None and validation_delta is not None and train_delta > config.train_epsilon
            and validation_delta < -config.validation_epsilon)


def _has_tool_mismatch(case: CaseSnapshot) -> bool:
    return [_tool_names(item) for item in case.actual] != [_tool_names(item) for item in case.expected]


def _has_argument_mismatch(case: CaseSnapshot) -> bool:
    actual = [item.tool_calls for item in case.actual]
    expected = [item.tool_calls for item in case.expected]
    return bool(actual or expected) and actual != expected


def _tool_names(invocation) -> list[str]:
    return [call.get("name", "") for call in invocation.tool_calls]


def _contains_any(value: str, markers: Iterable[str]) -> bool:
    return any(marker in value for marker in markers)
