# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Convert SDK evaluation results into stable, lightweight analysis data."""

from __future__ import annotations

from collections.abc import Iterable
from statistics import mean

from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalMetricResultPerInvocation
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import get_all_tool_calls

from .schemas import CaseEvaluation
from .schemas import CaseRunOutcome
from .schemas import EvaluationStatus
from .schemas import FakeEvaluationSnapshot
from .schemas import InvocationEvidence
from .schemas import MetricOutcome
from .schemas import ObservableValue
from .schemas import StandardizedEvaluation
from .schemas import ToolCallEvidence


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


def standardize_snapshot(snapshot: FakeEvaluationSnapshot) -> StandardizedEvaluation:
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
