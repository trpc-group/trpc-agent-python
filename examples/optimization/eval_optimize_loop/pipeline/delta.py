# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Candidate regression delta analysis for the eval-optimize-loop example."""

from __future__ import annotations

from typing import Any

from .types import BaselineCaseRecord
from .types import BaselineSplitResult
from .types import CaseDelta
from .types import MetricDelta
from .types import OptimizationDelta
from .types import SplitDeltaSummary


class DeltaAnalyzer:
    """Compare baseline and candidate evaluation snapshots without re-running evaluators."""

    def analyze(
        self,
        *,
        train_baseline: BaselineSplitResult,
        train_candidate: BaselineSplitResult,
        val_baseline: BaselineSplitResult,
        val_candidate: BaselineSplitResult,
    ) -> OptimizationDelta:
        return OptimizationDelta(
            train=self.analyze_split(
                split="train",
                baseline=train_baseline,
                candidate=train_candidate,
            ),
            val=self.analyze_split(
                split="val",
                baseline=val_baseline,
                candidate=val_candidate,
            ),
        )

    def analyze_split(
        self,
        *,
        split: str,
        baseline: BaselineSplitResult,
        candidate: BaselineSplitResult,
    ) -> SplitDeltaSummary:
        baseline_by_id = {case.id: case for case in baseline.cases}
        candidate_by_id = {case.id: case for case in candidate.cases}
        baseline_ids = set(baseline_by_id)
        candidate_ids = set(candidate_by_id)
        missing_ids = sorted(baseline_ids - candidate_ids)
        extra_ids = sorted(candidate_ids - baseline_ids)

        case_deltas = [
            _case_delta(
                split=split,
                baseline=baseline_by_id[case_id],
                candidate=candidate_by_id.get(case_id),
            ) for case_id in sorted(baseline_ids)
        ]
        case_deltas.extend(
            _extra_candidate_delta(
                split=split,
                candidate=candidate_by_id[case_id],
            ) for case_id in extra_ids)

        return SplitDeltaSummary(
            split=split,
            baseline_score=_average_score(baseline.cases),
            candidate_score=_average_score(candidate.cases),
            score_delta=_round(_average_score(candidate.cases) - _average_score(baseline.cases)),
            baseline_pass_rate=_pass_rate(baseline.cases),
            candidate_pass_rate=_pass_rate(candidate.cases),
            pass_rate_delta=_round(_pass_rate(candidate.cases) - _pass_rate(baseline.cases)),
            case_deltas=case_deltas,
            missing_candidate_case_ids=missing_ids,
            extra_candidate_case_ids=extra_ids,
        )


def _case_delta(
    *,
    split: str,
    baseline: BaselineCaseRecord,
    candidate: BaselineCaseRecord | None,
) -> CaseDelta:
    if candidate is None:
        return CaseDelta(
            id=baseline.id,
            split=split,
            change_type="missing_candidate",
            baseline=_case_ref(baseline),
            candidate=_missing_ref(),
            score_delta=None,
            metric_deltas=_metric_deltas(baseline, None),
            latency_delta=None,
            cost_delta=None,
            regression=True,
            improvement=False,
            notes=["Candidate evaluation is missing this baseline case."],
        )

    metric_deltas = _metric_deltas(baseline, candidate)
    change_type = _change_type(baseline, candidate)
    metric_regression = any(metric.status_transition == "passed_to_failed" for metric in metric_deltas)
    metric_improvement = any(metric.status_transition == "failed_to_passed" for metric in metric_deltas)
    regression = change_type in {"new_fail", "score_down"} or metric_regression
    improvement = change_type in {"new_pass", "score_up"} or metric_improvement
    return CaseDelta(
        id=baseline.id,
        split=split,
        change_type=change_type,
        baseline=_case_ref(baseline),
        candidate=_case_ref(candidate),
        score_delta=_round(candidate.metric_score - baseline.metric_score),
        metric_deltas=metric_deltas,
        latency_delta=_round(candidate.latency - baseline.latency),
        cost_delta=_round(candidate.cost - baseline.cost),
        regression=regression,
        improvement=improvement,
    )


def _extra_candidate_delta(*, split: str, candidate: BaselineCaseRecord) -> CaseDelta:
    return CaseDelta(
        id=candidate.id,
        split=split,
        change_type="extra_candidate",
        baseline=_missing_ref(),
        candidate=_case_ref(candidate),
        score_delta=None,
        metric_deltas=_metric_deltas(None, candidate),
        latency_delta=None,
        cost_delta=None,
        regression=False,
        improvement=True,
        notes=["Candidate evaluation produced a case that is not present in baseline."],
    )


def _metric_deltas(
    baseline: BaselineCaseRecord | None,
    candidate: BaselineCaseRecord | None,
) -> list[MetricDelta]:
    baseline_metrics = _metric_statuses(baseline)
    candidate_metrics = _metric_statuses(candidate)
    metric_names = sorted(set(baseline_metrics) | set(candidate_metrics))
    return [
        MetricDelta(
            metric_name=name,
            baseline_score=baseline_metrics.get(name, {}).get("score"),
            candidate_score=candidate_metrics.get(name, {}).get("score"),
            score_delta=_metric_score_delta(baseline_metrics.get(name), candidate_metrics.get(name)),
            baseline_passed=baseline_metrics.get(name, {}).get("passed"),
            candidate_passed=candidate_metrics.get(name, {}).get("passed"),
            status_transition=_status_transition(
                baseline_metrics.get(name, {}).get("passed"),
                candidate_metrics.get(name, {}).get("passed"),
            ),
        ) for name in metric_names
    ]


def _metric_statuses(case: BaselineCaseRecord | None) -> dict[str, dict[str, Any]]:
    if case is None:
        return {}
    metadata = case.evaluator_metadata or {}
    metadata_metrics = metadata.get("overall_metric_results") or []
    statuses: dict[str, dict[str, Any]] = {}
    if isinstance(metadata_metrics, list):
        for metric in metadata_metrics:
            if not isinstance(metric, dict):
                continue
            name = str(metric.get("metric_name") or "")
            if not name:
                continue
            statuses[name] = {
                "score": metric.get("score"),
                "passed": _metric_passed(metric),
            }
    for name, score in case.metric_scores.items():
        statuses.setdefault(name, {
            "score": score,
            "passed": score >= 1.0,
        })
    return statuses


def _metric_passed(metric: dict[str, Any]) -> bool | None:
    status = str(metric.get("eval_status") or "").upper()
    if status == "PASSED":
        return True
    if status in {"FAILED", "NOT_EVALUATED"}:
        return False
    score = metric.get("score")
    threshold = metric.get("threshold")
    if isinstance(score, (int, float)) and isinstance(threshold, (int, float)):
        return score >= threshold
    return None


def _metric_score_delta(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> float | None:
    if baseline is None or candidate is None:
        return None
    baseline_score = baseline.get("score")
    candidate_score = candidate.get("score")
    if not isinstance(baseline_score, (int, float)) or not isinstance(candidate_score, (int, float)):
        return None
    return _round(candidate_score - baseline_score)


def _status_transition(
    baseline_passed: bool | None,
    candidate_passed: bool | None,
) -> str:
    if baseline_passed is None and candidate_passed is None:
        return "missing_both"
    if baseline_passed is None:
        return "missing_baseline"
    if candidate_passed is None:
        return "missing_candidate"
    if baseline_passed and candidate_passed:
        return "passed_to_passed"
    if baseline_passed and not candidate_passed:
        return "passed_to_failed"
    if not baseline_passed and candidate_passed:
        return "failed_to_passed"
    return "failed_to_failed"


def _change_type(baseline: BaselineCaseRecord, candidate: BaselineCaseRecord) -> str:
    if not baseline.passed and candidate.passed:
        return "new_pass"
    if baseline.passed and not candidate.passed:
        return "new_fail"
    if candidate.metric_score > baseline.metric_score:
        return "score_up"
    if candidate.metric_score < baseline.metric_score:
        return "score_down"
    return "unchanged"


def _case_ref(case: BaselineCaseRecord) -> dict[str, Any]:
    return {
        "passed": case.passed,
        "metric_score": case.metric_score,
        "failure_category": _failure_category(case),
        "latency": case.latency,
        "cost": case.cost,
    }


def _missing_ref() -> dict[str, Any]:
    return {
        "passed": None,
        "metric_score": None,
        "failure_category": None,
        "latency": None,
        "cost": None,
    }


def _failure_category(case: BaselineCaseRecord) -> str | None:
    if case.failure_analysis is None:
        return None
    return case.failure_analysis.category


def _average_score(cases: list[BaselineCaseRecord]) -> float:
    if not cases:
        return 0.0
    return _round(sum(case.metric_score for case in cases) / len(cases))


def _pass_rate(cases: list[BaselineCaseRecord]) -> float:
    if not cases:
        return 0.0
    return _round(sum(1 for case in cases if case.passed) / len(cases))


def _round(value: float) -> float:
    return round(value, 6)
