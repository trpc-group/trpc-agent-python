# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for RougeEvaluator."""

import pytest
from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import PrebuiltMetrics
from trpc_agent_sdk.evaluation import RougeEvaluator


class TestRougeEvaluator:
    """Test suite for RougeEvaluator."""

    @pytest.fixture
    def skip_if_no_rouge(self):
        """Skip test if rouge_score not installed."""
        try:
            import rouge_score  # noqa: F401
        except ImportError:
            pytest.skip("rouge-score not installed")

    def test_init_with_eval_metric(self, skip_if_no_rouge):
        """Test constructor with EvalMetric."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.RESPONSE_MATCH_SCORE.value,
            threshold=0.8,
        )
        ev = RougeEvaluator(eval_metric=m)
        assert ev._threshold == 0.8

    def test_init_both_raises(self, skip_if_no_rouge):
        """Test that both threshold and eval_metric raises ValueError."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.RESPONSE_MATCH_SCORE.value,
            threshold=0.5,
        )
        with pytest.raises(ValueError):
            RougeEvaluator(threshold=0.8, eval_metric=m)

    def test_get_metric_info(self, skip_if_no_rouge):
        """Test get_metric_info returns correct metric name."""
        info = RougeEvaluator.get_metric_info()
        assert info.metric_name == PrebuiltMetrics.RESPONSE_MATCH_SCORE.value
        assert info.metric_value_info.interval is not None
        assert info.metric_value_info.interval.min_value == 0.0
        assert info.metric_value_info.interval.max_value == 1.0
