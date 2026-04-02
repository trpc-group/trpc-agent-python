# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluation metrics (_eval_metrics)."""

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import Interval
from trpc_agent_sdk.evaluation import MetricInfo
from trpc_agent_sdk.evaluation import MetricValueInfo
from trpc_agent_sdk.evaluation import PrebuiltMetrics


class TestEvalStatus:
    """Test suite for EvalStatus enum."""

    def test_values(self):
        """Test enum values."""
        assert EvalStatus.PASSED.value == 1
        assert EvalStatus.FAILED.value == 2
        assert EvalStatus.NOT_EVALUATED.value == 3


class TestPrebuiltMetrics:
    """Test suite for PrebuiltMetrics enum."""

    def test_tool_trajectory(self):
        """Test tool_trajectory_avg_score value."""
        assert PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value == "tool_trajectory_avg_score"

    def test_final_response(self):
        """Test final_response_avg_score value."""
        assert PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value == "final_response_avg_score"

    def test_llm_metrics(self):
        """Test LLM metric names."""
        assert PrebuiltMetrics.LLM_FINAL_RESPONSE.value == "llm_final_response"
        assert PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value == "llm_rubric_response"
        assert PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value == "llm_rubric_knowledge_recall"


class TestInterval:
    """Test suite for Interval model."""

    def test_closed_interval(self):
        """Test closed interval [0, 1]."""
        iv = Interval(min_value=0.0, max_value=1.0)
        assert iv.min_value == 0.0
        assert iv.max_value == 1.0
        assert iv.open_at_min is False
        assert iv.open_at_max is False

    def test_open_interval(self):
        """Test open ends."""
        iv = Interval(min_value=0.0, max_value=1.0, open_at_min=True, open_at_max=True)
        assert iv.open_at_min is True
        assert iv.open_at_max is True


class TestEvalMetric:
    """Test suite for EvalMetric model."""

    def test_minimal_metric(self):
        """Test metric with required fields only."""
        m = EvalMetric(metric_name="my_metric", threshold=0.8)
        assert m.metric_name == "my_metric"
        assert m.threshold == 0.8
        assert m.criterion is None

    def test_metric_with_criterion(self):
        """Test metric with criterion dict."""
        m = EvalMetric(
            metric_name="llm_final_response",
            threshold=1.0,
            criterion={"llm_judge": {"judge_model": {"model_name": "glm-4"}}},
        )
        assert m.criterion is not None
        assert m.criterion.get("llm_judge", {}).get("judge_model", {}).get("model_name") == "glm-4"


class TestMetricInfo:
    """Test suite for MetricInfo model."""

    def test_metric_info(self):
        """Test MetricInfo creation."""
        info = MetricInfo(
            metric_name="tool_trajectory_avg_score",
            description="Compares tool call trajectories.",
            metric_value_info=MetricValueInfo(
                interval=Interval(min_value=0.0, max_value=1.0)
            ),
        )
        assert info.metric_name == "tool_trajectory_avg_score"
        assert info.metric_value_info.interval.min_value == 0.0
