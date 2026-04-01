# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Build EvalSetResultSummary and EvalSetResult from eval case results."""

from __future__ import annotations

import time
from collections import defaultdict

from ._eval_metrics import EvalStatus
from ._eval_result import EvalCaseResult
from ._eval_result import EvalCaseResultSummary
from ._eval_result import EvalCaseRunSummary
from ._eval_result import EvalMetricResult
from ._eval_result import EvalMetricRunSummary
from ._eval_result import EvalMetricSummary
from ._eval_result import EvalSetResult
from ._eval_result import EvalSetResultSummary
from ._eval_result import EvalSetRunSummary
from ._eval_result import EvalStatusCounts


def _sanitize_eval_set_result_name(eval_set_result_name: str) -> str:
    """Return name with '/' replaced by '_' for safe use in paths."""
    return eval_set_result_name.replace("/", "_")


def _add_status(counts: EvalStatusCounts, status: EvalStatus) -> None:
    """Increment the corresponding count in counts for the given status."""
    if status == EvalStatus.PASSED:
        counts.passed += 1
    elif status == EvalStatus.FAILED:
        counts.failed += 1
    else:
        counts.not_evaluated += 1


def _overall_status_from_counts(counts: EvalStatusCounts) -> EvalStatus:
    """Return FAILED if any failed, else PASSED if any passed, else NOT_EVALUATED."""
    if counts.failed > 0:
        return EvalStatus.FAILED
    if counts.passed > 0:
        return EvalStatus.PASSED
    return EvalStatus.NOT_EVALUATED


def _normalize_counts(counts: EvalStatusCounts) -> EvalStatusCounts | None:
    """Return None if all counts are zero, else counts."""
    if counts.passed == 0 and counts.failed == 0 and counts.not_evaluated == 0:
        return None
    return counts


def _merge_metric_agg(
    agg: dict[str, dict],
    metric_results: list[EvalMetricResult],
) -> None:
    """Merge metric_results into agg: per-metric threshold, count, score sum, status counts."""
    for m in metric_results:
        if m is None:
            continue
        name = m.metric_name
        if name not in agg:
            agg[name] = {
                "threshold": m.threshold or 0.0,
                "evaluated_count": 0,
                "score_sum": 0.0,
                "status_counts": EvalStatusCounts(),
            }
        ent = agg[name]
        _add_status(ent["status_counts"], m.eval_status)
        if m.eval_status != EvalStatus.NOT_EVALUATED:
            ent["evaluated_count"] += 1
            ent["score_sum"] += (m.score or 0.0)


def _build_metric_summaries(agg: dict[str, dict]) -> list[EvalMetricSummary]:
    """Build EvalMetricSummary list from agg (average_score, eval_status, status_counts)."""
    if not agg:
        return []
    out: list[EvalMetricSummary] = []
    for name in sorted(agg.keys()):
        ent = agg[name]
        counts = ent["status_counts"]
        avg_score = 0.0
        eval_status = EvalStatus.NOT_EVALUATED
        if ent["evaluated_count"] > 0:
            avg_score = ent["score_sum"] / ent["evaluated_count"]
            thresh = ent["threshold"]
            eval_status = (EvalStatus.PASSED if avg_score >= thresh else EvalStatus.FAILED)
        out.append(
            EvalMetricSummary(
                metric_name=name,
                average_score=avg_score,
                eval_status=eval_status,
                threshold=ent["threshold"],
                status_counts=_normalize_counts(counts),
            ))
    return out


def _build_metric_run_summaries(metric_results: list[EvalMetricResult], ) -> list[EvalMetricRunSummary]:
    """Build EvalMetricRunSummary list from metric_results, sorted by metric_name."""
    if not metric_results:
        return []
    return [
        EvalMetricRunSummary(
            metric_name=m.metric_name,
            score=m.score or 0.0,
            eval_status=m.eval_status,
            threshold=m.threshold or 0.0,
        ) for m in sorted(metric_results, key=lambda x: x.metric_name) if m is not None
    ]


def _summarize_overall_from_metric_summaries(
    metric_summaries: list[EvalMetricSummary],
    has_run_error: bool,
) -> EvalStatus:
    """Overall status from metric summaries; FAILED if any failed or has_run_error with no statuses."""
    statuses = [s.eval_status for s in metric_summaries if s is not None]
    if not statuses:
        return EvalStatus.FAILED if has_run_error else EvalStatus.NOT_EVALUATED
    failed = any(s == EvalStatus.FAILED for s in statuses)
    passed = any(s == EvalStatus.PASSED for s in statuses)
    if failed:
        return EvalStatus.FAILED
    if passed:
        return EvalStatus.PASSED
    return EvalStatus.FAILED if has_run_error else EvalStatus.NOT_EVALUATED


def build_eval_set_result_summary(
    eval_case_results: list[EvalCaseResult],
    expected_num_runs: int | None = None,
) -> EvalSetResultSummary | None:
    """Build EvalSetResultSummary from eval_case_results: run/case/metric structure and status counts.
    Returns None if eval_case_results is empty."""
    if not eval_case_results:
        return None
    run_results: dict[int, list[EvalCaseResult]] = defaultdict(list)
    for r in eval_case_results:
        if r.run_id is not None:
            run_results[r.run_id].append(r)
    if not run_results:
        return None
    num_runs = expected_num_runs or max(run_results)
    if num_runs < 1:
        num_runs = 1
    run_ids = list(range(1, num_runs + 1))
    run_status_counts = EvalStatusCounts()
    run_summaries_list: list[EvalSetRunSummary] = []
    for run_id in run_ids:
        cases = run_results.get(run_id, [])
        case_status_counts = EvalStatusCounts()
        run_metric_agg: dict[str, dict] = {}
        for c in cases:
            _add_status(case_status_counts, c.final_eval_status)
            _add_status(run_status_counts, c.final_eval_status)
            _merge_metric_agg(run_metric_agg, c.overall_eval_metric_results)
        run_status = _overall_status_from_counts(case_status_counts)
        run_summaries_list.append(
            EvalSetRunSummary(
                run_id=run_id,
                overall_status=run_status,
                case_status_counts=_normalize_counts(case_status_counts),
                metric_summaries=_build_metric_summaries(run_metric_agg),
            ))
    overall_status = _overall_status_from_counts(run_status_counts)
    eval_ids = sorted({c.eval_id for c in eval_case_results})
    eval_case_summaries_list: list[EvalCaseResultSummary] = []
    for eval_id in eval_ids:
        case_list = [c for c in eval_case_results if c.eval_id == eval_id]
        case_counts = EvalStatusCounts()
        case_metric_agg: dict[str, dict] = {}
        has_run_error = any(c.error_message for c in case_list if c.error_message)
        run_summaries_for_case: list[EvalCaseRunSummary] = []
        for c in case_list:
            _add_status(case_counts, c.final_eval_status)
            _merge_metric_agg(case_metric_agg, c.overall_eval_metric_results)
            if c.run_id is not None:
                run_summaries_for_case.append(
                    EvalCaseRunSummary(
                        run_id=c.run_id,
                        final_eval_status=c.final_eval_status,
                        error_message=c.error_message,
                        metric_results=_build_metric_run_summaries(c.overall_eval_metric_results),
                    ))
        run_summaries_for_case.sort(key=lambda x: x.run_id)
        metric_summaries = _build_metric_summaries(case_metric_agg)
        case_overall = _summarize_overall_from_metric_summaries(metric_summaries, has_run_error)
        eval_case_summaries_list.append(
            EvalCaseResultSummary(
                eval_id=eval_id,
                overall_status=case_overall,
                run_status_counts=_normalize_counts(case_counts),
                metric_summaries=metric_summaries,
                run_summaries=run_summaries_for_case,
            ))
    return EvalSetResultSummary(
        overall_status=overall_status,
        num_runs=num_runs,
        run_status_counts=_normalize_counts(run_status_counts),
        run_summaries=run_summaries_list,
        eval_case_summaries=eval_case_summaries_list,
    )


def create_eval_set_result(
    app_name: str,
    eval_set_id: str,
    eval_case_results: list[EvalCaseResult],
    expected_num_runs: int | None = None,
) -> EvalSetResult:
    """Create EvalSetResult from eval_case_results; summary built when multiple runs or cases.

    Args:
        app_name: Application name.
        eval_set_id: Eval set id.
        eval_case_results: Per-case results (all runs).
        expected_num_runs: Run count for summary; inferred from results if omitted.

    Returns:
        EvalSetResult with id, name, results, summary, timestamp.
    """
    timestamp = time.time()
    eval_set_result_id = f"{app_name}_{eval_set_id}_{timestamp}"
    eval_set_result_name = _sanitize_eval_set_result_name(eval_set_result_id)
    summary = build_eval_set_result_summary(eval_case_results, expected_num_runs)
    eval_set_result = EvalSetResult(
        eval_set_result_id=eval_set_result_id,
        eval_set_result_name=eval_set_result_name,
        eval_set_id=eval_set_id,
        eval_case_results=eval_case_results,
        summary=summary,
        creation_timestamp=timestamp,
    )
    return eval_set_result
