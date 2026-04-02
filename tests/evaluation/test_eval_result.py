# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluation result types (_eval_result)."""

import pytest

pytest.importorskip("trpc_agent_sdk._runners", reason="trpc_agent_sdk._runners not yet implemented")

from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalMetricResultDetails
from trpc_agent_sdk.evaluation import EvalSetAggregateResult
from trpc_agent_sdk.evaluation import EvalStatusCounts
from trpc_agent_sdk.evaluation import EvaluateResult
from trpc_agent_sdk.evaluation import EvaluationResult


class TestEvalMetricResultDetails:
    """Test suite for EvalMetricResultDetails."""

    def test_empty_details(self):
        """Test default details."""
        d = EvalMetricResultDetails()
        assert d.reason is None
        assert d.score is None
        assert d.rubric_scores is None

    def test_details_with_reason(self):
        """Test details with reason."""
        d = EvalMetricResultDetails(reason="Answer is correct.", score=1.0)
        assert d.reason == "Answer is correct."
        assert d.score == 1.0


class TestEvalMetricResult:
    """Test suite for EvalMetricResult."""

    def test_metric_result_minimal(self):
        """Test EvalMetricResult with required fields."""
        r = EvalMetricResult(
            metric_name="final_response_avg_score",
            threshold=0.8,
            score=1.0,
            eval_status=EvalStatus.PASSED,
        )
        assert r.metric_name == "final_response_avg_score"
        assert r.threshold == 0.8
        assert r.score == 1.0
        assert r.eval_status == EvalStatus.PASSED
        assert r.details is None

    def test_metric_result_with_details(self):
        """Test EvalMetricResult with details (reason)."""
        r = EvalMetricResult(
            metric_name="llm_final_response",
            threshold=1.0,
            score=1.0,
            eval_status=EvalStatus.PASSED,
            details=EvalMetricResultDetails(reason="Valid response."),
        )
        assert r.details is not None
        assert r.details.reason == "Valid response."


class TestEvalCaseResult:
    """Test suite for EvalCaseResult."""

    def test_case_result_minimal(self):
        """Test EvalCaseResult with minimal fields."""
        emr = EvalMetricResult(
            metric_name="m1",
            threshold=0.5,
            score=0.8,
            eval_status=EvalStatus.PASSED,
        )
        c = EvalCaseResult(
            eval_set_id="set1",
            eval_id="case_001",
            final_eval_status=EvalStatus.PASSED,
            overall_eval_metric_results=[emr],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )
        assert c.eval_set_id == "set1"
        assert c.eval_id == "case_001"
        assert len(c.overall_eval_metric_results) == 1
        assert c.overall_eval_metric_results[0].metric_name == "m1"
        assert c.session_id == "s1"


class TestEvaluationResult:
    """Test suite for EvaluationResult."""

    def test_evaluation_result_empty(self):
        """Test empty EvaluationResult."""
        r = EvaluationResult()
        assert r.overall_score is None
        assert r.overall_eval_status == EvalStatus.NOT_EVALUATED
        assert r.per_invocation_results == []


class TestEvalStatusCounts:
    """Test suite for EvalStatusCounts."""

    def test_counts_default(self):
        """Test default counts are zero."""
        c = EvalStatusCounts()
        assert c.passed == 0
        assert c.failed == 0
        assert c.not_evaluated == 0

    def test_counts_values(self):
        """Test custom counts."""
        c = EvalStatusCounts(passed=2, failed=1, not_evaluated=0)
        assert c.passed == 2
        assert c.failed == 1


class TestEvalSetAggregateResult:
    """Test suite for EvalSetAggregateResult."""

    def test_aggregate_result_empty(self):
        """Test empty aggregate result."""
        a = EvalSetAggregateResult()
        assert a.eval_results_by_eval_id == {}
        assert a.num_runs == 1


class TestEvaluateResult:
    """Test suite for EvaluateResult."""

    def test_evaluate_result_empty(self):
        """Test empty EvaluateResult."""
        r = EvaluateResult()
        assert r.results_by_eval_set_id == {}
