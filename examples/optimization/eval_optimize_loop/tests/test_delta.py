# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Delta analysis tests."""

from __future__ import annotations

from pipeline import DeltaAnalyzer
from pipeline.types import BaselineSplitResult

from .conftest import case_record
from .conftest import metric_result


def test_delta_analyzer_covers_regression_improvement_and_case_set_mismatch() -> None:
    baseline = BaselineSplitResult(
        eval_set_id="synthetic_base",
        cases=[
            case_record("new_fail", passed=True, metric_results=[metric_result("m", score=1.0, eval_status="PASSED")]),
            case_record("score_up", passed=True, metric_results=[metric_result("m", score=0.5, eval_status="PASSED")]),
            case_record("score_down", passed=True, metric_results=[metric_result("m", score=0.9,
                                                                                 eval_status="PASSED")]),
            case_record("unchanged", passed=True, metric_results=[metric_result("m", score=1.0, eval_status="PASSED")]),
            case_record("missing", passed=True, metric_results=[metric_result("m", score=1.0, eval_status="PASSED")]),
        ],
    )
    candidate = BaselineSplitResult(
        eval_set_id="synthetic_candidate",
        cases=[
            case_record("new_fail", passed=False, metric_results=[metric_result("m", score=0.0, eval_status="FAILED")]),
            case_record("score_up", passed=True, metric_results=[metric_result("m", score=0.8, eval_status="PASSED")]),
            case_record("score_down", passed=True, metric_results=[metric_result("m", score=0.4,
                                                                                 eval_status="PASSED")]),
            case_record("unchanged", passed=True, metric_results=[metric_result("m", score=1.0, eval_status="PASSED")]),
            case_record("extra", passed=True, metric_results=[metric_result("m", score=1.0, eval_status="PASSED")]),
        ],
    )

    split_delta = DeltaAnalyzer().analyze_split(split="train", baseline=baseline, candidate=candidate)
    by_id = {case.id: case for case in split_delta.case_deltas}

    assert by_id["new_fail"].change_type == "new_fail"
    assert by_id["new_fail"].regression is True
    assert by_id["score_up"].change_type == "score_up"
    assert by_id["score_up"].improvement is True
    assert by_id["score_down"].change_type == "score_down"
    assert by_id["score_down"].regression is True
    assert by_id["unchanged"].change_type == "unchanged"
    assert by_id["missing"].change_type == "missing_candidate"
    assert by_id["extra"].change_type == "extra_candidate"
    assert split_delta.missing_candidate_case_ids == ["missing"]
    assert split_delta.extra_candidate_case_ids == ["extra"]
