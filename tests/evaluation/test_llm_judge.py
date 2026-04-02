# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for LLM judge (_llm_judge)."""

from unittest.mock import Mock

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import PerInvocationResult
from trpc_agent_sdk.evaluation import Rubric
from trpc_agent_sdk.evaluation import RubricContent
from trpc_agent_sdk.evaluation import ScoreResult
from trpc_agent_sdk.evaluation import AverageInvocationsAggregator
from trpc_agent_sdk.evaluation import MajorityVoteSamplesAggregator
from trpc_agent_sdk.evaluation._llm_judge import _extract_rubrics_text


class TestMajorityVoteSamplesAggregator:
    """Test suite for MajorityVoteSamplesAggregator."""

    def test_empty_samples_raises(self):
        """Test aggregate_samples with empty list raises."""
        agg = MajorityVoteSamplesAggregator()
        with pytest.raises(ValueError):
            agg.aggregate_samples([], threshold=0.5)

    def test_single_sample_returns_it(self):
        """Test single sample is returned as-is."""
        agg = MajorityVoteSamplesAggregator()
        s = ScoreResult(score=1.0, reason="ok")
        out = agg.aggregate_samples([s], threshold=0.5)
        assert out == s

    def test_majority_passed_returns_passed(self):
        """Test majority passed returns a passed sample."""
        agg = MajorityVoteSamplesAggregator()
        passed = ScoreResult(score=1.0, reason="p")
        failed = ScoreResult(score=0.0, reason="f")
        out = agg.aggregate_samples([failed, passed, passed], threshold=0.5)
        assert out.score >= 0.5

    def test_majority_failed_returns_failed(self):
        """Test majority failed returns a failed sample."""
        agg = MajorityVoteSamplesAggregator()
        passed = ScoreResult(score=1.0, reason="p")
        failed = ScoreResult(score=0.0, reason="f")
        out = agg.aggregate_samples([passed, failed, failed], threshold=0.5)
        assert out.score < 0.5


class TestAverageInvocationsAggregator:
    """Test suite for AverageInvocationsAggregator."""

    def test_all_not_evaluated_returns_not_evaluated(self):
        """Test results with no evaluated scores returns (None, NOT_EVALUATED)."""
        agg = AverageInvocationsAggregator()
        inv_mock = Mock(spec=Invocation)
        r1 = PerInvocationResult(actual_invocation=inv_mock, score=None, eval_status=EvalStatus.NOT_EVALUATED)
        score, status = agg.aggregate_invocations([r1], threshold=0.5)
        assert score is None
        assert status == EvalStatus.NOT_EVALUATED

    def test_average_above_threshold_returns_passed(self):
        """Test average above threshold returns PASSED."""
        agg = AverageInvocationsAggregator()
        inv_mock = Mock(spec=Invocation)
        r1 = PerInvocationResult(actual_invocation=inv_mock, score=0.8, eval_status=EvalStatus.PASSED)
        r2 = PerInvocationResult(actual_invocation=inv_mock, score=0.6, eval_status=EvalStatus.PASSED)
        score, status = agg.aggregate_invocations([r1, r2], threshold=0.5)
        assert score == 0.7
        assert status == EvalStatus.PASSED

    def test_below_threshold_returns_failed(self):
        """Test average below threshold returns FAILED."""
        agg = AverageInvocationsAggregator()
        inv_mock = Mock(spec=Invocation)
        r1 = PerInvocationResult(actual_invocation=inv_mock, score=0.3, eval_status=EvalStatus.FAILED)
        r2 = PerInvocationResult(actual_invocation=inv_mock, score=0.4, eval_status=EvalStatus.FAILED)
        score, status = agg.aggregate_invocations([r1, r2], threshold=0.5)
        assert score == 0.35
        assert status == EvalStatus.FAILED


class TestExtractRubricsText:
    """Test suite for _extract_rubrics_text."""

    def test_empty_rubrics(self):
        """Test empty or None rubrics returns empty string."""
        assert _extract_rubrics_text([]) == ""
        assert _extract_rubrics_text(None) == ""

    def test_single_rubric(self):
        """Test single rubric formatted as id: content.text."""
        rubrics = [
            Rubric(
                id="1",
                content=RubricContent(text="Answer must include temperature."),
                description="Temp",
                type="QUALITY",
            ),
        ]
        out = _extract_rubrics_text(rubrics)
        assert "1:" in out
        assert "Answer must include temperature." in out

    def test_multiple_rubrics(self):
        """Test multiple rubrics on separate lines."""
        rubrics = [
            Rubric(id="1", content=RubricContent(text="First."), description="", type=""),
            Rubric(id="2", content=RubricContent(text="Second."), description="", type=""),
        ]
        out = _extract_rubrics_text(rubrics)
        assert "1: First." in out
        assert "2: Second." in out
        assert out.count("\n") == 1
