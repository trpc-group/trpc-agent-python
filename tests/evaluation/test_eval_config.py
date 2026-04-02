# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluation config (_eval_config)."""

import pytest

pytest.importorskip("trpc_agent_sdk._runners", reason="trpc_agent_sdk._runners not yet implemented")

from trpc_agent_sdk.evaluation import EvalConfig
from trpc_agent_sdk.evaluation._eval_config import _normalize_criterion_for_metric
from trpc_agent_sdk.evaluation._eval_config import _threshold_from_value
from trpc_agent_sdk.evaluation import PrebuiltMetrics


class TestNormalizeCriterionForMetric:
    """Test suite for _normalize_criterion_for_metric."""

    def test_non_dict_returns_none(self):
        """Test non-dict value returns None."""
        assert _normalize_criterion_for_metric("m1", None) is None
        assert _normalize_criterion_for_metric("m1", 1.0) is None

    def test_criterion_key_returned(self):
        """Test criterion or Criterion key is returned."""
        c = {"toolTrajectory": {"order_sensitive": True}}
        assert _normalize_criterion_for_metric(
            "m1", {"criterion": c}
        ) == c
        assert _normalize_criterion_for_metric(
            "m1", {"Criterion": c}
        ) == c

    def test_strategy_tool_trajectory(self):
        """Test strategy builds toolTrajectory for tool_trajectory_avg_score."""
        s = {"order_sensitive": True}
        out = _normalize_criterion_for_metric(
            PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value,
            {"strategy": s},
        )
        assert out == {"toolTrajectory": s}

    def test_strategy_final_response(self):
        """Test strategy builds finalResponse for final_response_avg_score."""
        s = {"match": "contains"}
        out = _normalize_criterion_for_metric(
            PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value,
            {"strategy": s},
        )
        assert out == {"finalResponse": s}


class TestThresholdFromValue:
    """Test suite for _threshold_from_value."""

    def test_number_returns_float(self):
        """Test int/float returns float."""
        assert _threshold_from_value(0.8) == 0.8
        assert _threshold_from_value(1) == 1.0

    def test_dict_with_threshold(self):
        """Test dict with threshold or Threshold key."""
        assert _threshold_from_value({"threshold": 0.5}) == 0.5
        assert _threshold_from_value({"Threshold": 0.9}) == 0.9

    def test_dict_without_threshold_returns_default(self):
        """Test dict without threshold returns 1.0."""
        assert _threshold_from_value({}) == 1.0
        assert _threshold_from_value("x") == 1.0


class TestEvalConfig:
    """Test suite for EvalConfig."""

    def test_get_eval_metrics_from_criteria(self):
        """Test get_eval_metrics from criteria dict."""
        config = EvalConfig(
            criteria={
                "tool_trajectory_avg_score": 0.8,
                "final_response_avg_score": {"threshold": 1.0},
            },
        )
        metrics = config.get_eval_metrics()
        assert len(metrics) == 2
        names = {m.metric_name for m in metrics}
        assert "tool_trajectory_avg_score" in names
        assert "final_response_avg_score" in names
        for m in metrics:
            if m.metric_name == "tool_trajectory_avg_score":
                assert m.threshold == 0.8
            else:
                assert m.threshold == 1.0

    def test_get_eval_metrics_from_metrics_array(self):
        """Test get_eval_metrics from metrics array (camelCase)."""
        config = EvalConfig(
            metrics=[
                {"metricName": "m1", "threshold": 0.5},
                {"metric_name": "m2", "threshold": 1.0},
            ],
        )
        metrics = config.get_eval_metrics()
        assert len(metrics) == 2
        assert metrics[0].metric_name == "m1"
        assert metrics[0].threshold == 0.5
        assert metrics[1].metric_name == "m2"
        assert metrics[1].threshold == 1.0

    def test_num_runs_default(self):
        """Test num_runs default is 1."""
        config = EvalConfig(criteria={})
        assert config.num_runs == 1
