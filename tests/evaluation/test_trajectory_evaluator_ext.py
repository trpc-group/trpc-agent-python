# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Extended tests for TrajectoryEvaluator."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import TrajectoryEvaluator
from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import PrebuiltMetrics
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall


def _make_invocation(tool_calls=None):
    from trpc_agent_sdk.evaluation._eval_case import IntermediateData
    intermediate = None
    if tool_calls is not None:
        intermediate = IntermediateData(tool_uses=tool_calls)
    return Invocation(user_content=Content(parts=[]), intermediate_data=intermediate)


class TestTrajectoryEvaluatorInit:
    """Test suite for TrajectoryEvaluator initialization."""

    def test_with_threshold(self):
        """Test init with threshold."""
        e = TrajectoryEvaluator(threshold=0.5)
        assert e._threshold == 0.5

    def test_with_eval_metric(self):
        """Test init with eval_metric."""
        m = EvalMetric(metric_name=PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value, threshold=0.8)
        e = TrajectoryEvaluator(eval_metric=m)
        assert e._threshold == 0.8

    def test_both_raises(self):
        """Test passing both threshold and eval_metric raises."""
        m = EvalMetric(metric_name="m", threshold=0.5)
        with pytest.raises(ValueError, match="Either"):
            TrajectoryEvaluator(threshold=0.5, eval_metric=m)


class TestTrajectoryEvaluatorEvaluate:
    """Test suite for TrajectoryEvaluator.evaluate_invocations."""

    def test_none_expected_raises(self):
        """Test None expected raises ValueError."""
        e = TrajectoryEvaluator(threshold=0.5)
        with pytest.raises(ValueError, match="required"):
            e.evaluate_invocations([], None)

    def test_empty_invocations(self):
        """Test empty invocations."""
        e = TrajectoryEvaluator(threshold=0.5)
        result = e.evaluate_invocations([], [])
        assert result.overall_score is None

    def test_matching_tool_calls(self):
        """Test matching tool calls produce 1.0."""
        e = TrajectoryEvaluator(threshold=0.5)
        fc = FunctionCall(name="get_weather", args={"city": "Beijing"})
        actual = _make_invocation([fc])
        expected = _make_invocation([fc])
        result = e.evaluate_invocations([actual], [expected])
        assert result.overall_score == 1.0
        assert result.overall_eval_status == EvalStatus.PASSED

    def test_mismatched_tool_calls(self):
        """Test mismatched tool calls produce 0.0."""
        e = TrajectoryEvaluator(threshold=0.5)
        a = _make_invocation([FunctionCall(name="foo", args={})])
        exp = _make_invocation([FunctionCall(name="bar", args={})])
        result = e.evaluate_invocations([a], [exp])
        assert result.overall_score == 0.0
        assert result.overall_eval_status == EvalStatus.FAILED

    def test_different_length_fails(self):
        """Test different number of tool calls per invocation fails."""
        e = TrajectoryEvaluator(threshold=0.5)
        a = _make_invocation([FunctionCall(name="foo", args={}), FunctionCall(name="bar", args={})])
        exp = _make_invocation([FunctionCall(name="foo", args={})])
        result = e.evaluate_invocations([a], [exp])
        assert result.overall_score == 0.0

    def test_with_criterion(self):
        """Test with ToolTrajectoryCriterion from eval_metric."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value,
            threshold=0.5,
            criterion={"toolTrajectory": {"order_sensitive": False}},
        )
        e = TrajectoryEvaluator(eval_metric=m)
        a = _make_invocation([FunctionCall(name="b", args={}), FunctionCall(name="a", args={})])
        exp = _make_invocation([FunctionCall(name="a", args={}), FunctionCall(name="b", args={})])
        result = e.evaluate_invocations([a], [exp])
        assert result.overall_score == 1.0


class TestTrajectoryEvaluatorMetricInfo:
    """Test suite for TrajectoryEvaluator.get_metric_info."""

    def test_metric_info(self):
        """Test get_metric_info returns correct info."""
        info = TrajectoryEvaluator.get_metric_info()
        assert info.metric_name == PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value
        assert info.metric_value_info is not None


class TestToolCallsEqual:
    """Test suite for _are_tool_calls_equal."""

    def test_equal(self):
        """Test equal tool calls."""
        e = TrajectoryEvaluator(threshold=0.5)
        fc1 = FunctionCall(name="a", args={"x": 1})
        fc2 = FunctionCall(name="a", args={"x": 1})
        assert e._are_tool_calls_equal([fc1], [fc2]) is True

    def test_different_args(self):
        """Test different args."""
        e = TrajectoryEvaluator(threshold=0.5)
        fc1 = FunctionCall(name="a", args={"x": 1})
        fc2 = FunctionCall(name="a", args={"x": 2})
        assert e._are_tool_calls_equal([fc1], [fc2]) is False

    def test_different_names(self):
        """Test different names."""
        e = TrajectoryEvaluator(threshold=0.5)
        fc1 = FunctionCall(name="a", args={})
        fc2 = FunctionCall(name="b", args={})
        assert e._are_tool_calls_equal([fc1], [fc2]) is False
