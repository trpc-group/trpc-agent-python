# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for FinalResponseEvaluator."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import PrebuiltMetrics
from trpc_agent_sdk.evaluation import FinalResponseEvaluator


class TestFinalResponseEvaluator:
    """Test suite for FinalResponseEvaluator."""

    def test_init_with_threshold(self):
        """Test constructor with threshold only."""
        ev = FinalResponseEvaluator(threshold=0.9)
        assert ev._threshold == 0.9
        assert ev._criterion is not None

    def test_init_with_eval_metric(self):
        """Test constructor with EvalMetric."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value,
            threshold=1.0,
        )
        ev = FinalResponseEvaluator(eval_metric=m)
        assert ev._threshold == 1.0

    def test_init_both_raises(self):
        """Test that both threshold and eval_metric raises ValueError."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value,
            threshold=0.5,
        )
        with pytest.raises(ValueError) as exc_info:
            FinalResponseEvaluator(threshold=0.8, eval_metric=m)
        assert "eval_metric" in str(exc_info.value).lower() or "threshold" in str(exc_info.value).lower()

    def test_get_metric_info(self):
        """Test get_metric_info returns correct metric name."""
        info = FinalResponseEvaluator.get_metric_info()
        assert info.metric_name == PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value
        assert info.metric_value_info.interval is not None
        assert info.metric_value_info.interval.min_value == 0.0
        assert info.metric_value_info.interval.max_value == 1.0
