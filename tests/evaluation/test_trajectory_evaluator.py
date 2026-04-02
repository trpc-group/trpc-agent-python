# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for TrajectoryEvaluator."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import PrebuiltMetrics
from trpc_agent_sdk.evaluation import TrajectoryEvaluator


class TestTrajectoryEvaluator:
    """Test suite for TrajectoryEvaluator."""

    def test_init_with_threshold(self):
        """Test constructor with threshold only."""
        ev = TrajectoryEvaluator(threshold=0.8)
        assert ev._threshold == 0.8
        assert ev._trajectory_criterion is None

    def test_init_with_eval_metric(self):
        """Test constructor with EvalMetric."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value,
            threshold=0.5,
        )
        ev = TrajectoryEvaluator(eval_metric=m)
        assert ev._threshold == 0.5

    def test_init_both_raises(self):
        """Test that both threshold and eval_metric raises ValueError."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value,
            threshold=0.5,
        )
        with pytest.raises(ValueError) as exc_info:
            TrajectoryEvaluator(threshold=0.8, eval_metric=m)
        assert "eval_metric" in str(exc_info.value).lower() or "threshold" in str(exc_info.value).lower()

    def test_get_metric_info(self):
        """Test get_metric_info returns correct metric name."""
        info = TrajectoryEvaluator.get_metric_info()
        assert info.metric_name == PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value
        assert info.metric_value_info.interval is not None
        assert info.metric_value_info.interval.min_value == 0.0
        assert info.metric_value_info.interval.max_value == 1.0
