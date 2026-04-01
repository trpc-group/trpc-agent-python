# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluator registry (_evaluator_registry)."""

import pytest
from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import PrebuiltMetrics
from trpc_agent_sdk.evaluation import EVALUATOR_REGISTRY
from trpc_agent_sdk.evaluation import EvaluatorRegistry


class TestEvaluatorRegistry:
    """Test suite for EvaluatorRegistry."""

    def test_list_registered(self):
        """Test list_registered returns sorted metric names."""
        names = EVALUATOR_REGISTRY.list_registered()
        assert isinstance(names, list)
        assert PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value in names
        assert PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value in names
        assert PrebuiltMetrics.LLM_FINAL_RESPONSE.value in names
        assert names == sorted(names)

    def test_get_evaluator_tool_trajectory(self):
        """Test get_evaluator for tool_trajectory_avg_score."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value,
            threshold=0.8,
        )
        ev = EVALUATOR_REGISTRY.get_evaluator(m)
        assert ev is not None
        assert ev._threshold == 0.8

    def test_get_evaluator_final_response(self):
        """Test get_evaluator for final_response_avg_score."""
        m = EvalMetric(
            metric_name=PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value,
            threshold=1.0,
        )
        ev = EVALUATOR_REGISTRY.get_evaluator(m)
        assert ev is not None
        assert ev._threshold == 1.0

    def test_get_evaluator_unknown_raises(self):
        """Test get_evaluator for unregistered metric raises."""
        m = EvalMetric(metric_name="unknown_metric", threshold=0.5)
        with pytest.raises(ValueError) as exc_info:
            EVALUATOR_REGISTRY.get_evaluator(m)
        assert "unknown_metric" in str(exc_info.value)
        assert "registered" in str(exc_info.value).lower() or "available" in str(exc_info.value).lower()

    def test_register_custom(self):
        """Test register adds custom evaluator and get_evaluator uses it."""
        from trpc_agent_sdk.evaluation._evaluator_base import Evaluator
        from trpc_agent_sdk.evaluation._eval_result import EvaluationResult
        from trpc_agent_sdk.evaluation._eval_case import Invocation

        class DummyEvaluator(Evaluator):
            def __init__(self, eval_metric=None):
                self._metric = eval_metric

            def evaluate_invocations(self, actual_invocations, expected_invocations):
                return EvaluationResult(overall_score=1.0)

        reg = EvaluatorRegistry()
        reg.register("dummy_metric", DummyEvaluator)
        assert "dummy_metric" in reg.list_registered()
        m = EvalMetric(metric_name="dummy_metric", threshold=0.5)
        ev = reg.get_evaluator(m)
        assert isinstance(ev, DummyEvaluator)
