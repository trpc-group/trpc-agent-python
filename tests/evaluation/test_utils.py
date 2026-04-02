# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluation result handler and utils (_utils)."""

import pytest

pytest.importorskip("trpc_agent_sdk._runners", reason="trpc_agent_sdk._runners not yet implemented")

from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import EvalResultHandler
from trpc_agent_sdk.evaluation._utils import RESULT_LABELS
from trpc_agent_sdk.evaluation._utils import _result_label_width


class TestResultLabelWidth:
    """Test suite for _result_label_width."""

    def test_result_label_width(self):
        """Test width is max of RESULT_LABELS lengths."""
        w = _result_label_width()
        assert w >= len("Agent Name")
        assert w == max(len(l) for l in RESULT_LABELS)


class TestEvalResultHandler:
    """Test suite for EvalResultHandler."""

    @pytest.fixture
    def handler(self):
        """Create EvalResultHandler instance."""
        return EvalResultHandler()

    def test_eval_status_str_passed(self, handler):
        """Test eval_status_str for PASSED."""
        assert handler.eval_status_str(EvalStatus.PASSED) == "passed"

    def test_eval_status_str_failed(self, handler):
        """Test eval_status_str for FAILED."""
        assert handler.eval_status_str(EvalStatus.FAILED) == "failed"

    def test_eval_status_str_not_evaluated(self, handler):
        """Test eval_status_str for NOT_EVALUATED."""
        assert handler.eval_status_str(EvalStatus.NOT_EVALUATED) == "not_evaluated"

    def test_format_number_like_json_none(self, handler):
        """Test format_number_like_json with None."""
        assert handler.format_number_like_json(None) == "N/A"

    def test_format_number_like_json_number(self, handler):
        """Test format_number_like_json with number."""
        assert handler.format_number_like_json(0.8) == "0.8"
        assert handler.format_number_like_json(1.0) == "1.0"

    def test_build_summary_empty_results(self, handler):
        """Test build_summary with empty eval_results_by_eval_id."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[])
        summary = handler.build_summary(
            eval_set=eval_set,
            eval_results_by_eval_id={},
            agent_name="test_agent",
            num_runs=1,
        )
        assert summary["agent_name"] == "test_agent"
        assert summary["eval_set_id"] == "set1"
        assert summary["overall_status"] == "passed"
        assert summary["runs"] == 1
        assert summary["eval_cases"] == []

    def test_build_summary_single_case_passed(self, handler):
        """Test build_summary with one case that passed."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[])
        emr = EvalMetricResult(
            metric_name="m1",
            threshold=0.5,
            score=0.8,
            eval_status=EvalStatus.PASSED,
        )
        ecr = EvalCaseResult(
            eval_set_id="set1",
            eval_id="case_001",
            final_eval_status=EvalStatus.PASSED,
            overall_eval_metric_results=[emr],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )
        summary = handler.build_summary(
            eval_set=eval_set,
            eval_results_by_eval_id={"case_001": [ecr]},
            agent_name="agent1",
            num_runs=1,
        )
        assert summary["overall_status"] == "passed"
        assert len(summary["eval_cases"]) == 1
        case = summary["eval_cases"][0]
        assert case["eval_case_id"] == "case_001"
        assert case["overall_status"] == "passed"
        assert len(case["metric_results"]) == 1
        mr = case["metric_results"][0]
        assert mr["metric_name"] == "m1"
        assert mr["score"] == 0.8
        assert mr["threshold"] == 0.5
        assert mr["eval_status"] == "passed"

    def test_build_summary_single_case_failed(self, handler):
        """Test build_summary with one case that failed (score below threshold)."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[])
        emr = EvalMetricResult(
            metric_name="m1",
            threshold=0.8,
            score=0.3,
            eval_status=EvalStatus.FAILED,
        )
        ecr = EvalCaseResult(
            eval_set_id="set1",
            eval_id="case_001",
            final_eval_status=EvalStatus.FAILED,
            overall_eval_metric_results=[emr],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )
        summary = handler.build_summary(
            eval_set=eval_set,
            eval_results_by_eval_id={"case_001": [ecr]},
            agent_name="agent1",
            num_runs=1,
        )
        assert summary["overall_status"] == "failed"
        assert summary["eval_cases"][0]["overall_status"] == "failed"
        assert summary["eval_cases"][0]["metric_results"][0]["eval_status"] == "failed"

    def test_build_summary_multiple_runs_average(self, handler):
        """Test build_summary with two runs; score is averaged."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[])
        ecr1 = EvalCaseResult(
            eval_set_id="set1",
            eval_id="case_001",
            final_eval_status=EvalStatus.PASSED,
            overall_eval_metric_results=[
                EvalMetricResult(
                    metric_name="m1",
                    threshold=0.5,
                    score=0.6,
                    eval_status=EvalStatus.PASSED,
                ),
            ],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )
        ecr2 = EvalCaseResult(
            eval_set_id="set1",
            eval_id="case_001",
            final_eval_status=EvalStatus.PASSED,
            overall_eval_metric_results=[
                EvalMetricResult(
                    metric_name="m1",
                    threshold=0.5,
                    score=0.8,
                    eval_status=EvalStatus.PASSED,
                ),
            ],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )
        summary = handler.build_summary(
            eval_set=eval_set,
            eval_results_by_eval_id={"case_001": [ecr1, ecr2]},
            agent_name="agent1",
            num_runs=2,
        )
        assert summary["runs"] == 2
        mr = summary["eval_cases"][0]["metric_results"][0]
        assert mr["score"] == 0.7  # (0.6 + 0.8) / 2
        assert mr["eval_status"] == "passed"

    def test_summary_to_export_dict(self, handler):
        """Test summary_to_export_dict produces camelCase keys."""
        summary = {
            "agent_name": "a1",
            "eval_set_id": "set1",
            "overall_status": "passed",
            "runs": 1,
            "eval_cases": [
                {
                    "eval_case_id": "c1",
                    "overall_status": "passed",
                    "metric_results": [
                        {
                            "metric_name": "m1",
                            "score": 0.9,
                            "threshold": 0.5,
                            "eval_status": "passed",
                        },
                    ],
                },
            ],
        }
        out = handler.summary_to_export_dict(summary)
        assert out["agentName"] == "a1"
        assert out["evalSetId"] == "set1"
        assert out["overallStatus"] == "passed"
        assert out["runs"] == 1
        assert len(out["evalCases"]) == 1
        assert out["evalCases"][0]["evalCaseId"] == "c1"
        assert out["evalCases"][0]["metricResults"][0]["metricName"] == "m1"
        assert out["evalCases"][0]["metricResults"][0]["evalStatus"] == "passed"

    def test_build_evaluation_result_lines(self, handler):
        """Test build_evaluation_result_lines output format."""
        summary = {
            "agent_name": "test_agent",
            "eval_set_id": "eval_set_1",
            "overall_status": "passed",
            "runs": 1,
            "eval_cases": [
                {
                    "eval_case_id": "case_001",
                    "overall_status": "passed",
                    "metric_results": [
                        {
                            "metric_name": "m1",
                            "score": 1.0,
                            "threshold": 0.8,
                            "eval_status": "passed",
                        },
                    ],
                },
            ],
        }
        lines = handler.build_evaluation_result_lines(
            summary,
            include_completed_line=True,
            include_agent_runs=True,
        )
        assert any("Eval Set" in line and "eval_set_1" in line for line in lines)
        assert any("Overall Status" in line and "passed" in line for line in lines)
        assert any("Case case_001" in line and "passed" in line for line in lines)
        assert any("Metric m1" in line and "1.0" in line and "0.8" in line for line in lines)

    def test_build_evaluation_result_lines_no_header(self, handler):
        """Test build_evaluation_result_lines without completed/runs lines."""
        summary = {
            "agent_name": "a",
            "eval_set_id": "s",
            "overall_status": "passed",
            "runs": 1,
            "eval_cases": [],
        }
        lines = handler.build_evaluation_result_lines(
            summary,
            include_completed_line=False,
            include_agent_runs=False,
        )
        assert any("Eval Set" in line for line in lines)
        assert any("Overall Status" in line for line in lines)
