# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for criterion registry (_criterion_registry)."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import CRITERION_REGISTRY
from trpc_agent_sdk.evaluation import CriterionRegistry
from trpc_agent_sdk.evaluation import CriterionType
from trpc_agent_sdk.evaluation import PrebuiltMetrics


class TestCriterionType:
    """Test suite for CriterionType enum."""

    def test_values(self):
        """Test CriterionType values."""
        assert CriterionType.TEXT.value == "text"
        assert CriterionType.FINAL_RESPONSE.value == "final_response"
        assert CriterionType.TOOL_TRAJECTORY.value == "tool_trajectory"
        assert CriterionType.LLM_JUDGE.value == "llm_judge"


class TestCriterionRegistry:
    """Test suite for CriterionRegistry."""

    @pytest.fixture
    def registry(self):
        """Fresh registry to avoid affecting global."""
        return CriterionRegistry()

    def test_build_none_returns_none(self, registry):
        """Test build(None) returns None."""
        assert registry.build(None) is None
        assert registry.build(None, metric_key="m1") is None

    def test_build_non_dict_returns_none(self, registry):
        """Test build with non-dict returns None."""
        assert registry.build("x") is None

    def test_build_tool_trajectory_from_metric_key(self, registry):
        """Test build returns ToolTrajectoryCriterion for tool_trajectory_avg_score."""
        config = {"tool_trajectory": {"order_sensitive": True}}
        c = registry.build(config, metric_key=PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value)
        assert c is not None
        assert hasattr(c, "matches")
        assert getattr(c, "order_sensitive", None) is True

    def test_build_final_response_from_metric_key(self, registry):
        """Test build returns FinalResponseCriterion for final_response_avg_score."""
        config = {"finalResponse": {"text": {"match": "contains"}}}
        c = registry.build(config, metric_key=PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value)
        assert c is not None
        assert hasattr(c, "matches")

    def test_register_override(self, registry):
        """Test register replaces built-in with custom match rule."""
        def my_match(actual, expected):
            return actual == expected and actual == "ok"
        registry.register(CriterionType.TEXT, my_match)
        config = {"type": "text"}
        c = registry.build(config)
        assert c is not None
        assert c.matches("ok", "ok") is True
        assert c.matches("no", "no") is False

    def test_list_registered_not_used_on_fresh(self):
        """Test CRITERION_REGISTRY has built-in factories (build works)."""
        c = CRITERION_REGISTRY.build(
            {"finalResponse": {"text": {"match": "exact"}}},
            metric_key="final_response_avg_score",
        )
        assert c is not None
