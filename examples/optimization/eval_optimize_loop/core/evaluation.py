# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Evaluation normalization, attribution, case diff, and analysis."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean
from typing import Literal

from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalMetricResultPerInvocation
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import get_all_tool_calls

from ..data.schemas import AttributionEvidence
from ..data.schemas import CaseDiff
from ..data.schemas import CaseEvaluation
from ..data.schemas import CaseRunOutcome
from ..data.schemas import ChangeKind
from ..data.schemas import DatasetDiff
from ..data.schemas import EvaluationAnalysis
from ..data.schemas import EvaluationSnapshot
from ..data.schemas import EvaluationStatus
from ..data.schemas import FailureAttribution
from ..data.schemas import FailureCategory
from ..data.schemas import InvocationEvidence
from ..data.schemas import MetricDelta
from ..data.schemas import MetricOutcome
from ..data.schemas import ObservableValue
from ..data.schemas import OverfitStatus
from ..data.schemas import StandardizedEvaluation
from ..data.schemas import ToolCallEvidence


class EvaluationAnalysisError(ValueError):
    """Evaluation evidence is structurally inconsistent and unsafe to compare."""


def _status(value: EvalStatus, *, error_message: str | None = None) -> EvaluationStatus:
    if error_message or value == EvalStatus.NOT_EVALUATED:
        return "not_evaluated"
    if value == EvalStatus.PASSED:
        return "passed"
    return "failed"


def _content_text(content: object | None) -> str | None:
    if content is None:
        return None
    parts = getattr(content, "parts", None) or []
    text = "\n".join(part.text for part in parts if getattr(part, "text", None))
    return text or None


def _tool_evidence(invocation: Invocation | None) -> list[ToolCallEvidence]:
    if invocation is None:
        return []
    return [
        ToolCallEvidence(name=call.name or "", arguments=dict(call.args or {}))
        for call in get_all_tool_calls(invocation.intermediate_data)
    ]


def _observable(scores: Iterable[float | None], *, reason: str) -> ObservableValue:
    values = list(scores)
    if not values or any(score is None for score in values):
        return ObservableValue(status="unavailable", reason=reason)
    return ObservableValue(status="available", value=mean(float(score) for score in values))


def _metric_map(metrics: list[EvalMetricResult], *, context: str) -> dict[str, EvalMetricResult]:
    result: dict[str, EvalMetricResult] = {}
    for metric in metrics:
        if metric.metric_name in result:
            raise EvaluationAnalysisError(f"{context} contains duplicate metric {metric.metric_name!r}")
        result[metric.metric_name] = metric
    return result


def _metric_outcome(metric: EvalMetricResult, *, context: str) -> MetricOutcome:
    reason = metric.details.reason if metric.details is not None else None
    score = _observable([metric.score], reason=f"{context} metric score is unavailable")
    return MetricOutcome(
        metric_name=metric.metric_name,
        threshold=metric.threshold,
        status="not_evaluated" if metric.score is None else _status(metric.eval_status),
        score=score,
        reason=reason,
    )


def _invocation_evidence(result: EvalMetricResultPerInvocation, *, context: str) -> InvocationEvidence:
    actual = result.actual_invocation
    expected = result.expected_invocation
    metrics = _metric_map(result.eval_metric_results, context=context)
    return InvocationEvidence(
        invocation_id=actual.invocation_id,
        user_text=_content_text(actual.user_content) or "",
        expected_response=_content_text(expected.final_response) if expected is not None else None,
        actual_response=_content_text(actual.final_response),
        expected_tools=_tool_evidence(expected),
        actual_tools=_tool_evidence(actual),
        metrics=[_metric_outcome(metrics[name], context=context) for name in sorted(metrics)],
    )


def _case_evaluation(
    eval_id: str,
    raw_runs: list[EvalCaseResult],
    *,
    eval_set_id: str,
) -> CaseEvaluation:
    if not raw_runs:
        raise EvaluationAnalysisError(f"case {eval_id!r} has no run results")

    ordered_runs = sorted(raw_runs, key=lambda run: run.run_id if run.run_id is not None else 0)
    run_ids = [run.run_id if run.run_id is not None else index for index, run in enumerate(ordered_runs, 1)]
    if len(run_ids) != len(set(run_ids)):
        raise EvaluationAnalysisError(f"case {eval_id!r} contains duplicate run ids")

    metric_maps: list[dict[str, EvalMetricResult]] = []
    normalized_runs: list[CaseRunOutcome] = []
    for run_id, run in zip(run_ids, ordered_runs):
        if run.eval_id != eval_id:
            raise EvaluationAnalysisError(
                f"case mapping key {eval_id!r} does not match result eval_id {run.eval_id!r}"
            )
        if run.eval_set_id != eval_set_id:
            raise EvaluationAnalysisError(
                f"case {eval_id!r} run {run_id} has eval_set_id {run.eval_set_id!r}; "
                f"expected {eval_set_id!r}"
            )
        context = f"case {eval_id!r} run {run_id}"
        metric_map = _metric_map(run.overall_eval_metric_results, context=context)
        metric_maps.append(metric_map)
        normalized_metrics = [
            _metric_outcome(metric_map[name], context=context) for name in sorted(metric_map)
        ]
        run_status = _status(run.final_eval_status, error_message=run.error_message)
        if not normalized_metrics or any(metric.status == "not_evaluated" for metric in normalized_metrics):
            run_status = "not_evaluated"
        normalized_runs.append(
            CaseRunOutcome(
                run_id=run_id,
                status=run_status,
                error_message=run.error_message,
                metrics=normalized_metrics,
                invocations=[
                    _invocation_evidence(invocation, context=f"{context} invocation {index}")
                    for index, invocation in enumerate(run.eval_metric_result_per_invocation, 1)
                ],
            )
        )

    metric_names = sorted(set().union(*(metrics.keys() for metrics in metric_maps)))
    aggregate_metrics: list[MetricOutcome] = []
    for name in metric_names:
        present = [metrics.get(name) for metrics in metric_maps]
        thresholds = {metric.threshold for metric in present if metric is not None}
        if len(thresholds) > 1:
            raise EvaluationAnalysisError(f"case {eval_id!r} metric {name!r} has inconsistent thresholds")
        available_metrics = [metric for metric in present if metric is not None]
        metric_status: EvaluationStatus
        if len(available_metrics) != len(present) or any(
            metric.eval_status == EvalStatus.NOT_EVALUATED or metric.score is None for metric in available_metrics
        ):
            metric_status = "not_evaluated"
        elif all(metric.eval_status == EvalStatus.PASSED for metric in available_metrics):
            metric_status = "passed"
        else:
            metric_status = "failed"
        reasons = [
            metric.details.reason
            for metric in available_metrics
            if metric.details is not None and metric.details.reason
        ]
        aggregate_metrics.append(
            MetricOutcome(
                metric_name=name,
                threshold=next(iter(thresholds), 0.0),
                status=metric_status,
                score=_observable(
                    [metric.score if metric is not None else None for metric in present],
                    reason=f"case {eval_id!r} metric {name!r} is unavailable in one or more runs",
                ),
                reason="; ".join(reasons) or None,
            )
        )

    statuses = [run.status for run in normalized_runs]
    if "not_evaluated" in statuses or any(metric.status == "not_evaluated" for metric in aggregate_metrics):
        case_status: EvaluationStatus = "not_evaluated"
    elif all(status == "passed" for status in statuses):
        case_status = "passed"
    else:
        case_status = "failed"
    return CaseEvaluation(
        eval_id=eval_id,
        status=case_status,
        average_score=_observable(
            [metric.score.value if metric.score.status == "available" else None for metric in aggregate_metrics],
            reason=f"case {eval_id!r} has unavailable metric scores",
        ),
        metrics=aggregate_metrics,
        runs=normalized_runs,
    )


def standardize_snapshot(snapshot: EvaluationSnapshot) -> StandardizedEvaluation:
    """Normalize one complete SDK snapshot without discarding raw evidence."""
    cases = [
        _case_evaluation(
            eval_id,
            snapshot.eval_results_by_eval_id[eval_id],
            eval_set_id=snapshot.eval_set_id,
        )
        for eval_id in sorted(snapshot.eval_results_by_eval_id)
    ]
    return StandardizedEvaluation(
        phase=snapshot.phase,
        split=snapshot.split,
        eval_set_id=snapshot.eval_set_id,
        cases=cases,
        passed_case_count=sum(case.status == "passed" for case in cases),
        failed_case_count=sum(case.status == "failed" for case in cases),
        not_evaluated_case_count=sum(case.status == "not_evaluated" for case in cases),
        average_score=_observable(
            [case.average_score.value if case.average_score.status == "available" else None for case in cases],
            reason="one or more case scores are unavailable",
        ),
    )


@dataclass(frozen=True)
class _CandidateReason:
    priority: int
    category: FailureCategory
    summary: str
    evidence: AttributionEvidence


def _json_object(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _case_attribution(case: CaseEvaluation) -> FailureAttribution | None:
    if case.status == "passed":
        return None

    reasons: list[_CandidateReason] = []
    for run in case.runs:
        if run.status == "not_evaluated" or run.error_message:
            summary = run.error_message or "Evaluation did not produce a usable result."
            reasons.append(
                _CandidateReason(
                    priority=10,
                    category="evaluation_error",
                    summary=summary,
                    evidence=AttributionEvidence(
                        evidence_type="execution_error",
                        message=summary,
                        run_id=run.run_id,
                        actual=run.error_message,
                    ),
                )
            )
        for invocation in run.invocations:
            expected_names = [tool.name for tool in invocation.expected_tools]
            actual_names = [tool.name for tool in invocation.actual_tools]
            if expected_names != actual_names and (expected_names or actual_names):
                summary = f"Expected tool names {expected_names}, got {actual_names}."
                reasons.append(
                    _CandidateReason(
                        priority=20,
                        category="tool_name_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="tool",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=expected_names,
                            actual=actual_names,
                        ),
                    )
                )

            expected_arguments = [tool.arguments for tool in invocation.expected_tools]
            actual_arguments = [tool.arguments for tool in invocation.actual_tools]
            if (
                expected_names == actual_names
                and (expected_names or actual_names)
                and expected_arguments != actual_arguments
            ):
                summary = f"Expected tool arguments {expected_arguments}, got {actual_arguments}."
                reasons.append(
                    _CandidateReason(
                        priority=30,
                        category="tool_argument_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="tool",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=expected_arguments,
                            actual=actual_arguments,
                        ),
                    )
                )

            failed_knowledge_metrics = [
                metric
                for metric in invocation.metrics
                if metric.status == "failed" and metric.metric_name == "llm_rubric_knowledge_recall"
            ]
            if failed_knowledge_metrics:
                metric = failed_knowledge_metrics[0]
                summary = metric.reason or "Knowledge recall rubric was not satisfied."
                reasons.append(
                    _CandidateReason(
                        priority=40,
                        category="knowledge_recall",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="metric",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            metric_name=metric.metric_name,
                            actual=metric.reason,
                        ),
                    )
                )

            expected_json = _json_object(invocation.expected_response)
            actual_json = _json_object(invocation.actual_response)
            if expected_json is not None and (
                actual_json is None or not set(expected_json).issubset(actual_json)
            ):
                summary = "Actual response is not valid JSON with the expected top-level fields."
                reasons.append(
                    _CandidateReason(
                        priority=50,
                        category="format_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="response",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=invocation.expected_response,
                            actual=invocation.actual_response,
                        ),
                    )
                )

            failed_rubric_metrics = [
                metric
                for metric in invocation.metrics
                if metric.status == "failed"
                and metric.metric_name.startswith("llm_rubric_")
                and metric.metric_name != "llm_rubric_knowledge_recall"
            ]
            if failed_rubric_metrics:
                metric = failed_rubric_metrics[0]
                summary = metric.reason or f"Rubric metric {metric.metric_name!r} was not satisfied."
                reasons.append(
                    _CandidateReason(
                        priority=60,
                        category="rubric_failure",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="metric",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            metric_name=metric.metric_name,
                            actual=metric.reason,
                        ),
                    )
                )

            if (
                expected_json is not None
                and actual_json is not None
                and expected_json.get("route") != actual_json.get("route")
            ):
                summary = (
                    f"Expected route {expected_json.get('route')!r}, "
                    f"got {actual_json.get('route')!r}."
                )
                reasons.append(
                    _CandidateReason(
                        priority=70,
                        category="routing_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="response",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=expected_json.get("route"),
                            actual=actual_json.get("route"),
                        ),
                    )
                )

            failed_final_metrics = [
                metric
                for metric in invocation.metrics
                if metric.status == "failed"
                and metric.metric_name
                in {"final_response_avg_score", "response_match_score", "llm_final_response"}
            ]
            if failed_final_metrics:
                metric = failed_final_metrics[0]
                summary = f"Final response did not satisfy metric {metric.metric_name!r}."
                reasons.append(
                    _CandidateReason(
                        priority=80,
                        category="final_response_mismatch",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="response",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            metric_name=metric.metric_name,
                            expected=invocation.expected_response,
                            actual=invocation.actual_response,
                        ),
                    )
                )

    if not reasons:
        summary = "Available evaluation evidence does not identify a specific failure category."
        reasons.append(
            _CandidateReason(
                priority=90,
                category="unknown",
                summary=summary,
                evidence=AttributionEvidence(evidence_type="metric", message=summary),
            )
        )

    reasons.sort(key=lambda reason: reason.priority)
    categories: list[FailureCategory] = []
    for reason in reasons:
        if reason.category not in categories:
            categories.append(reason.category)
    return FailureAttribution(
        primary_category=categories[0],
        secondary_categories=categories[1:],
        summary=reasons[0].summary,
        evidence=[reason.evidence for reason in reasons],
    )


def attribute_evaluation(evaluation: StandardizedEvaluation) -> StandardizedEvaluation:
    """Return a copy with deterministic attribution attached to failed cases."""
    return evaluation.model_copy(
        update={
            "cases": [
                case.model_copy(update={"attribution": _case_attribution(case)})
                for case in evaluation.cases
            ]
        }
    )


def _unavailable(reason: str) -> ObservableValue:
    return ObservableValue(status="unavailable", reason=reason)


def _delta(baseline: ObservableValue, candidate: ObservableValue, *, reason: str) -> ObservableValue:
    if baseline.status != "available" or candidate.status != "available":
        return _unavailable(reason)
    return ObservableValue(status="available", value=float(candidate.value) - float(baseline.value))


def _change(
    baseline_status: EvaluationStatus,
    candidate_status: EvaluationStatus,
    score_delta: ObservableValue,
) -> ChangeKind:
    if "not_evaluated" in {baseline_status, candidate_status}:
        return "incomparable"
    if baseline_status == "failed" and candidate_status == "passed":
        return "newly_passed"
    if baseline_status == "passed" and candidate_status == "failed":
        return "newly_failed"
    if score_delta.status != "available":
        return "incomparable"
    if float(score_delta.value) > 0.0 and not math.isclose(float(score_delta.value), 0.0, abs_tol=1e-12):
        return "improved"
    if float(score_delta.value) < 0.0 and not math.isclose(float(score_delta.value), 0.0, abs_tol=1e-12):
        return "regressed"
    return "unchanged"


def _case_metric_map(case: CaseEvaluation) -> dict[str, MetricOutcome]:
    return {metric.metric_name: metric for metric in case.metrics}


def _metric_deltas(baseline: CaseEvaluation, candidate: CaseEvaluation) -> list[MetricDelta]:
    baseline_metrics = _case_metric_map(baseline)
    candidate_metrics = _case_metric_map(candidate)
    if set(baseline_metrics) != set(candidate_metrics):
        raise EvaluationAnalysisError(
            f"case {baseline.eval_id!r} metric sets differ between baseline and candidate"
        )
    deltas: list[MetricDelta] = []
    for name in sorted(baseline_metrics):
        before = baseline_metrics[name]
        after = candidate_metrics[name]
        if before.threshold != after.threshold:
            raise EvaluationAnalysisError(
                f"case {baseline.eval_id!r} metric {name!r} threshold changed "
                f"from {before.threshold} to {after.threshold}"
            )
        score_delta = _delta(
            before.score,
            after.score,
            reason=f"case {baseline.eval_id!r} metric {name!r} score delta is unavailable",
        )
        deltas.append(
            MetricDelta(
                metric_name=name,
                baseline_status=before.status,
                candidate_status=after.status,
                baseline_score=before.score,
                candidate_score=after.score,
                score_delta=score_delta,
                change=_change(before.status, after.status, score_delta),
            )
        )
    return deltas


def _case_diff(
    baseline: CaseEvaluation,
    candidate: CaseEvaluation,
    *,
    split: Literal["train", "validation"],
    hard_case_ids: set[str],
    critical_case_ids: set[str],
    severe_case_score_drop: float,
) -> CaseDiff:
    score_delta = _delta(
        baseline.average_score,
        candidate.average_score,
        reason=f"case {baseline.eval_id!r} aggregate score delta is unavailable",
    )
    severe = (
        score_delta.status == "available"
        and float(score_delta.value) <= -severe_case_score_drop
        and not math.isclose(float(score_delta.value), 0.0, abs_tol=1e-12)
    )
    return CaseDiff(
        eval_id=baseline.eval_id,
        split=split,
        baseline_status=baseline.status,
        candidate_status=candidate.status,
        baseline_score=baseline.average_score,
        candidate_score=candidate.average_score,
        score_delta=score_delta,
        change=_change(baseline.status, candidate.status, score_delta),
        metrics=_metric_deltas(baseline, candidate),
        baseline_attribution=baseline.attribution,
        candidate_attribution=candidate.attribution,
        is_hard=baseline.eval_id in hard_case_ids,
        is_critical=baseline.eval_id in critical_case_ids,
        severe_regression=severe,
    )


def compare_evaluations(
    baseline: StandardizedEvaluation,
    candidate: StandardizedEvaluation,
    *,
    hard_case_ids: set[str],
    critical_case_ids: set[str],
    severe_case_score_drop: float,
) -> DatasetDiff:
    """Compare matching baseline and candidate evaluations for one split."""
    if baseline.phase != "baseline" or candidate.phase != "candidate":
        raise EvaluationAnalysisError("evaluation comparison requires baseline then candidate phases")
    if baseline.split != candidate.split:
        raise EvaluationAnalysisError("baseline and candidate splits do not match")
    if baseline.eval_set_id != candidate.eval_set_id:
        raise EvaluationAnalysisError("baseline and candidate eval_set_id values do not match")

    baseline_cases = {case.eval_id: case for case in baseline.cases}
    candidate_cases = {case.eval_id: case for case in candidate.cases}
    if set(baseline_cases) != set(candidate_cases):
        raise EvaluationAnalysisError("baseline and candidate case ids do not match")

    cases = [
        _case_diff(
            baseline_cases[eval_id],
            candidate_cases[eval_id],
            split=baseline.split,
            hard_case_ids=hard_case_ids,
            critical_case_ids=critical_case_ids,
            severe_case_score_drop=severe_case_score_drop,
        )
        for eval_id in sorted(baseline_cases)
    ]
    score_delta = _delta(
        baseline.average_score,
        candidate.average_score,
        reason=f"{baseline.split} dataset score delta is unavailable",
    )
    return DatasetDiff(
        split=baseline.split,
        eval_set_id=baseline.eval_set_id,
        cases=cases,
        baseline_average_score=baseline.average_score,
        candidate_average_score=candidate.average_score,
        score_delta=score_delta,
        newly_passed_count=sum(case.change == "newly_passed" for case in cases),
        newly_failed_count=sum(case.change == "newly_failed" for case in cases),
        improved_count=sum(case.change == "improved" for case in cases),
        regressed_count=sum(case.change == "regressed" for case in cases),
        unchanged_count=sum(case.change == "unchanged" for case in cases),
        incomparable_count=sum(case.change == "incomparable" for case in cases),
    )


def _overfit_status(
    train_delta: ObservableValue,
    validation_delta: ObservableValue,
) -> tuple[OverfitStatus, str]:
    if train_delta.status != "available" or validation_delta.status != "available":
        return "unavailable", "Train or validation score delta is unavailable."
    train_value = float(train_delta.value)
    validation_value = float(validation_delta.value)
    if train_value > 0.0 and validation_value < 0.0:
        return (
            "detected",
            f"Train score improved by {train_value:.6f} while validation regressed by "
            f"{validation_value:.6f}.",
        )
    return (
        "not_detected",
        f"Train score delta is {train_value:.6f}; validation score delta is "
        f"{validation_value:.6f}.",
    )


def build_evaluation_analysis(
    *,
    baseline_train: EvaluationSnapshot,
    baseline_validation: EvaluationSnapshot,
    candidate_train: EvaluationSnapshot,
    candidate_validation: EvaluationSnapshot,
    hard_case_ids: set[str],
    critical_case_ids: set[str],
    severe_case_score_drop: float,
) -> EvaluationAnalysis:
    """Build stage 3a analysis from the four complete evaluation snapshots."""
    normalized_baseline_train = attribute_evaluation(standardize_snapshot(baseline_train))
    normalized_baseline_validation = attribute_evaluation(standardize_snapshot(baseline_validation))
    normalized_candidate_train = attribute_evaluation(standardize_snapshot(candidate_train))
    normalized_candidate_validation = attribute_evaluation(standardize_snapshot(candidate_validation))

    train_diff = compare_evaluations(
        normalized_baseline_train,
        normalized_candidate_train,
        hard_case_ids=hard_case_ids,
        critical_case_ids=critical_case_ids,
        severe_case_score_drop=severe_case_score_drop,
    )
    validation_diff = compare_evaluations(
        normalized_baseline_validation,
        normalized_candidate_validation,
        hard_case_ids=hard_case_ids,
        critical_case_ids=critical_case_ids,
        severe_case_score_drop=severe_case_score_drop,
    )
    overfit_status, overfit_reason = _overfit_status(
        train_diff.score_delta,
        validation_diff.score_delta,
    )
    return EvaluationAnalysis(
        baseline_train=normalized_baseline_train,
        baseline_validation=normalized_baseline_validation,
        candidate_train=normalized_candidate_train,
        candidate_validation=normalized_candidate_validation,
        train_diff=train_diff,
        validation_diff=validation_diff,
        overfit_status=overfit_status,
        overfit_reason=overfit_reason,
    )
