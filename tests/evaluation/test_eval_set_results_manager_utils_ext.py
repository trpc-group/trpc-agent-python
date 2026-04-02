# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Extended tests for _eval_set_results_manager_utils: build_eval_set_result_summary, create_eval_set_result."""

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalStatusCounts
from trpc_agent_sdk.evaluation._eval_set_results_manager_utils import (
    _merge_metric_agg,
    _build_metric_summaries,
    _build_metric_run_summaries,
    _summarize_overall_from_metric_summaries,
    build_eval_set_result_summary,
    create_eval_set_result,
)


def _make_metric_result(name="m1", score=1.0, status=EvalStatus.PASSED, threshold=0.5):
    return EvalMetricResult(metric_name=name, score=score, eval_status=status, threshold=threshold)


def _make_case_result(eval_id="c1", run_id=1, status=EvalStatus.PASSED, metrics=None, error_message=None):
    return EvalCaseResult(
        eval_id=eval_id,
        run_id=run_id,
        final_eval_status=status,
        overall_eval_metric_results=metrics or [],
        eval_metric_result_per_invocation=[],
        session_id="s1",
        error_message=error_message,
    )


class TestMergeMetricAgg:
    """Test suite for _merge_metric_agg."""

    def test_empty_results(self):
        """Test merge with empty results does nothing."""
        agg = {}
        _merge_metric_agg(agg, [])
        assert agg == {}

    def test_none_in_results_skipped(self):
        """Test None items in results are skipped."""
        agg = {}
        _merge_metric_agg(agg, [None, _make_metric_result()])
        assert "m1" in agg
        assert agg["m1"]["evaluated_count"] == 1

    def test_not_evaluated_not_counted_in_score(self):
        """Test NOT_EVALUATED does not increment evaluated_count."""
        agg = {}
        _merge_metric_agg(agg, [_make_metric_result(status=EvalStatus.NOT_EVALUATED, score=0.0)])
        assert agg["m1"]["evaluated_count"] == 0
        assert agg["m1"]["score_sum"] == 0.0

    def test_multiple_metrics_merged(self):
        """Test multiple metrics with same name are merged."""
        agg = {}
        _merge_metric_agg(agg, [_make_metric_result(score=0.8), _make_metric_result(score=0.6)])
        assert agg["m1"]["evaluated_count"] == 2
        assert abs(agg["m1"]["score_sum"] - 1.4) < 1e-6

    def test_none_score_treated_as_zero(self):
        """Test None score treated as 0.0."""
        agg = {}
        _merge_metric_agg(agg, [_make_metric_result(score=None)])
        assert agg["m1"]["score_sum"] == 0.0

    def test_zero_threshold_treated_as_zero(self):
        """Test 0 threshold treated as 0.0."""
        agg = {}
        _merge_metric_agg(agg, [_make_metric_result(threshold=0.0)])
        assert agg["m1"]["threshold"] == 0.0


class TestBuildMetricSummaries:
    """Test suite for _build_metric_summaries."""

    def test_empty_agg(self):
        """Test empty agg returns empty list."""
        assert _build_metric_summaries({}) == []

    def test_single_metric_passed(self):
        """Test single metric above threshold produces PASSED."""
        agg = {}
        _merge_metric_agg(agg, [_make_metric_result(score=0.9, threshold=0.5)])
        summaries = _build_metric_summaries(agg)
        assert len(summaries) == 1
        assert summaries[0].eval_status == EvalStatus.PASSED
        assert abs(summaries[0].average_score - 0.9) < 1e-6

    def test_single_metric_failed(self):
        """Test single metric below threshold produces FAILED."""
        agg = {}
        _merge_metric_agg(agg, [_make_metric_result(score=0.3, threshold=0.5)])
        summaries = _build_metric_summaries(agg)
        assert summaries[0].eval_status == EvalStatus.FAILED

    def test_no_evaluated_produces_not_evaluated(self):
        """Test zero evaluated_count produces NOT_EVALUATED."""
        agg = {}
        _merge_metric_agg(agg, [_make_metric_result(status=EvalStatus.NOT_EVALUATED)])
        summaries = _build_metric_summaries(agg)
        assert summaries[0].eval_status == EvalStatus.NOT_EVALUATED


class TestBuildMetricRunSummaries:
    """Test suite for _build_metric_run_summaries."""

    def test_empty_list(self):
        """Test empty list returns empty."""
        assert _build_metric_run_summaries([]) == []

    def test_sorts_by_name(self):
        """Test results are sorted by metric_name."""
        m1 = _make_metric_result(name="z_metric")
        m2 = _make_metric_result(name="a_metric")
        result = _build_metric_run_summaries([m1, m2])
        assert result[0].metric_name == "a_metric"
        assert result[1].metric_name == "z_metric"

    def test_single_item(self):
        """Test single metric result produces one summary."""
        result = _build_metric_run_summaries([_make_metric_result()])
        assert len(result) == 1
        assert result[0].metric_name == "m1"


class TestSummarizeOverall:
    """Test suite for _summarize_overall_from_metric_summaries."""

    def test_empty_no_error(self):
        """Test empty summaries with no error returns NOT_EVALUATED."""
        assert _summarize_overall_from_metric_summaries([], False) == EvalStatus.NOT_EVALUATED

    def test_empty_with_error(self):
        """Test empty summaries with error returns FAILED."""
        assert _summarize_overall_from_metric_summaries([], True) == EvalStatus.FAILED

    def test_any_failed(self):
        """Test any FAILED metric returns FAILED."""
        from trpc_agent_sdk.evaluation._eval_result import EvalMetricSummary
        summaries = [
            EvalMetricSummary(metric_name="m1", average_score=0.9, eval_status=EvalStatus.PASSED, threshold=0.5),
            EvalMetricSummary(metric_name="m2", average_score=0.3, eval_status=EvalStatus.FAILED, threshold=0.5),
        ]
        assert _summarize_overall_from_metric_summaries(summaries, False) == EvalStatus.FAILED

    def test_all_passed(self):
        """Test all PASSED returns PASSED."""
        from trpc_agent_sdk.evaluation._eval_result import EvalMetricSummary
        summaries = [
            EvalMetricSummary(metric_name="m1", average_score=0.9, eval_status=EvalStatus.PASSED, threshold=0.5),
        ]
        assert _summarize_overall_from_metric_summaries(summaries, False) == EvalStatus.PASSED

    def test_not_evaluated_with_error(self):
        """Test all NOT_EVALUATED with error returns FAILED."""
        from trpc_agent_sdk.evaluation._eval_result import EvalMetricSummary
        summaries = [
            EvalMetricSummary(metric_name="m1", average_score=0.0, eval_status=EvalStatus.NOT_EVALUATED, threshold=0.5),
        ]
        assert _summarize_overall_from_metric_summaries(summaries, True) == EvalStatus.FAILED


class TestBuildEvalSetResultSummary:
    """Test suite for build_eval_set_result_summary."""

    def test_empty_results_returns_none(self):
        """Test empty list returns None."""
        assert build_eval_set_result_summary([]) is None

    def test_no_run_id_returns_none(self):
        """Test results without run_id returns None."""
        r = EvalCaseResult(eval_id="c1", run_id=None, final_eval_status=EvalStatus.PASSED,
                           overall_eval_metric_results=[], eval_metric_result_per_invocation=[],
                           session_id="s1")
        assert build_eval_set_result_summary([r]) is None

    def test_single_run_single_case(self):
        """Test single run with single case."""
        r = _make_case_result(eval_id="c1", run_id=1, status=EvalStatus.PASSED,
                              metrics=[_make_metric_result()])
        summary = build_eval_set_result_summary([r])
        assert summary is not None
        assert summary.num_runs == 1
        assert summary.overall_status == EvalStatus.PASSED
        assert len(summary.run_summaries) == 1
        assert len(summary.eval_case_summaries) == 1

    def test_multiple_runs(self):
        """Test multiple runs are aggregated."""
        r1 = _make_case_result(eval_id="c1", run_id=1, status=EvalStatus.PASSED, metrics=[_make_metric_result()])
        r2 = _make_case_result(eval_id="c1", run_id=2, status=EvalStatus.FAILED,
                               metrics=[_make_metric_result(score=0.3, status=EvalStatus.FAILED)])
        summary = build_eval_set_result_summary([r1, r2])
        assert summary.num_runs == 2
        assert summary.overall_status == EvalStatus.FAILED
        assert len(summary.run_summaries) == 2

    def test_expected_num_runs_overrides(self):
        """Test expected_num_runs overrides inferred runs."""
        r = _make_case_result(eval_id="c1", run_id=1, status=EvalStatus.PASSED)
        summary = build_eval_set_result_summary([r], expected_num_runs=3)
        assert summary.num_runs == 3
        assert len(summary.run_summaries) == 3

    def test_error_message_affects_case_summary(self):
        """Test error_message sets has_run_error in case summary."""
        r = _make_case_result(eval_id="c1", run_id=1, status=EvalStatus.NOT_EVALUATED,
                              error_message="boom")
        summary = build_eval_set_result_summary([r])
        assert summary.eval_case_summaries[0].overall_status == EvalStatus.FAILED

    def test_multiple_cases(self):
        """Test multiple eval cases produce separate summaries."""
        r1 = _make_case_result(eval_id="c1", run_id=1, status=EvalStatus.PASSED)
        r2 = _make_case_result(eval_id="c2", run_id=1, status=EvalStatus.FAILED)
        summary = build_eval_set_result_summary([r1, r2])
        assert len(summary.eval_case_summaries) == 2
        ids = [s.eval_id for s in summary.eval_case_summaries]
        assert "c1" in ids
        assert "c2" in ids


class TestCreateEvalSetResult:
    """Test suite for create_eval_set_result."""

    def test_basic_creation(self):
        """Test basic eval set result creation."""
        r = _make_case_result()
        result = create_eval_set_result("app1", "set1", [r])
        assert "app1" in result.eval_set_result_id
        assert "set1" in result.eval_set_result_id
        assert result.eval_set_id == "set1"
        assert len(result.eval_case_results) == 1
        assert result.summary is not None

    def test_slash_in_name_sanitized(self):
        """Test slash in app_name is sanitized in result name."""
        r = _make_case_result()
        result = create_eval_set_result("app/v1", "set/1", [r])
        assert "/" not in result.eval_set_result_name
