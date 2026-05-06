# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for evaluation result types (_eval_result)."""

import trpc_agent_sdk.runners  # noqa: F401

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


class TestNamedScoreResult:
    """Test suite for NamedScoreResult."""

    def test_minimal_construction(self):
        """Test NamedScoreResult with minimal fields uses defaults."""
        from trpc_agent_sdk.evaluation import NamedScoreResult

        n = NamedScoreResult(model_name="glm-4.7", score=1.0, passed=True)
        assert n.model_name == "glm-4.7"
        assert n.provider_name == ""
        assert n.score == 1.0
        assert n.reason == ""
        assert n.rubric_scores == []
        assert n.passed is True

    def test_full_construction_and_serialization(self):
        """Test all fields round-trip through JSON serialization."""
        from trpc_agent_sdk.evaluation import NamedScoreResult
        from trpc_agent_sdk.evaluation import RubricScore

        n = NamedScoreResult(
            model_name="gpt-4o",
            provider_name="openai",
            score=0.5,
            reason="half passed",
            rubric_scores=[RubricScore(id="r1", reason="ok", score=1.0)],
            passed=False,
        )
        data = n.model_dump()
        assert data["model_name"] == "gpt-4o"
        assert data["provider_name"] == "openai"
        assert data["passed"] is False
        assert data["rubric_scores"][0]["id"] == "r1"


class TestPerInvocationResultPerModelScores:
    """Test suite for PerInvocationResult.per_model_scores backward compatibility."""

    def test_default_is_none(self):
        """Test per_model_scores defaults to None for old code paths."""
        from unittest.mock import Mock

        from trpc_agent_sdk.evaluation import EvalStatus
        from trpc_agent_sdk.evaluation import Invocation
        from trpc_agent_sdk.evaluation import PerInvocationResult

        inv = Mock(spec=Invocation)
        r = PerInvocationResult(
            actual_invocation=inv,
            score=1.0,
            eval_status=EvalStatus.PASSED,
        )
        assert r.per_model_scores is None

    def test_per_model_scores_populated(self):
        """Test per_model_scores accepts list of NamedScoreResult."""
        from unittest.mock import Mock

        from trpc_agent_sdk.evaluation import EvalStatus
        from trpc_agent_sdk.evaluation import Invocation
        from trpc_agent_sdk.evaluation import NamedScoreResult
        from trpc_agent_sdk.evaluation import PerInvocationResult

        inv = Mock(spec=Invocation)
        per_model = [
            NamedScoreResult(model_name="m1", score=1.0, passed=True),
            NamedScoreResult(model_name="m2", score=0.0, passed=False),
        ]
        r = PerInvocationResult(
            actual_invocation=inv,
            score=0.0,
            eval_status=EvalStatus.FAILED,
            per_model_scores=per_model,
        )
        assert r.per_model_scores is not None
        assert len(r.per_model_scores) == 2
        assert r.per_model_scores[0].model_name == "m1"
        assert r.per_model_scores[1].passed is False
