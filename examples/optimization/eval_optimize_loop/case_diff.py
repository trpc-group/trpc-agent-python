# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Deterministic baseline/candidate case comparison for stage 3a."""

from __future__ import annotations

import math
from typing import Literal

from .evaluation_adapter import EvaluationAnalysisError
from .schemas import CaseDiff
from .schemas import CaseEvaluation
from .schemas import ChangeKind
from .schemas import DatasetDiff
from .schemas import EvaluationStatus
from .schemas import MetricDelta
from .schemas import MetricOutcome
from .schemas import ObservableValue
from .schemas import StandardizedEvaluation


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


def _metric_map(case: CaseEvaluation) -> dict[str, MetricOutcome]:
    return {metric.metric_name: metric for metric in case.metrics}


def _metric_deltas(baseline: CaseEvaluation, candidate: CaseEvaluation) -> list[MetricDelta]:
    baseline_metrics = _metric_map(baseline)
    candidate_metrics = _metric_map(candidate)
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
