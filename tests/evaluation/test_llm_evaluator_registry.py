# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LLM evaluator registry (llm_evaluator)."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import LLMEvaluatorRegistry
from trpc_agent_sdk.evaluation import LLM_METRIC_NAMES


class TestLLMEvaluatorRegistry:
    """Test suite for LLMEvaluatorRegistry."""

    @pytest.fixture
    def registry(self):
        """Fresh registry per test (avoid polluting global)."""
        return LLMEvaluatorRegistry()

    def test_register_judge_tools_llm_final_response(self, registry):
        """Test registering judge tools for llm_final_response."""
        tools = [lambda x: x]
        registry.register_judge_tools("llm_final_response", tools)
        assert registry.get_judge_tools("llm_final_response") == tools

    def test_register_judge_tools_llm_rubric_response(self, registry):
        """Test registering judge tools for llm_rubric_response."""
        tools = []
        registry.register_judge_tools("llm_rubric_response", tools)
        assert registry.get_judge_tools("llm_rubric_response") is not None
        assert registry.get_judge_tools("llm_rubric_response") == tools

    def test_register_judge_tools_invalid_metric_raises(self, registry):
        """Test that invalid metric_name raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            registry.register_judge_tools("invalid_metric", [])
        assert "invalid_metric" in str(exc_info.value)
        assert "must be one of" in str(exc_info.value).lower() or "llm_final_response" in str(exc_info.value)

    def test_get_judge_tools_unregistered_returns_none(self, registry):
        """Test get_judge_tools returns None when not registered."""
        assert registry.get_judge_tools("llm_final_response") is None
        assert registry.get_judge_tools("llm_rubric_knowledge_recall") is None

    def test_unregister_judge_tools(self, registry):
        """Test unregister_judge_tools removes tools."""
        registry.register_judge_tools("llm_final_response", [1, 2])
        assert registry.get_judge_tools("llm_final_response") is not None
        registry.unregister_judge_tools("llm_final_response")
        assert registry.get_judge_tools("llm_final_response") is None

    def test_llm_metric_names_contains_expected(self):
        """Test LLM_METRIC_NAMES contains expected metrics."""
        assert "llm_final_response" in LLM_METRIC_NAMES
        assert "llm_rubric_response" in LLM_METRIC_NAMES
        assert "llm_rubric_knowledge_recall" in LLM_METRIC_NAMES
        assert len(LLM_METRIC_NAMES) == 3


class TestModelsAggregatorRegistry:
    """Test suite for register_models_aggregator on LLMEvaluatorRegistry."""

    @pytest.fixture
    def registry(self):
        from trpc_agent_sdk.evaluation import LLMEvaluatorRegistry
        return LLMEvaluatorRegistry()

    def test_register_and_get(self, registry):
        """Test register_models_aggregator + get_models_aggregator round-trip."""
        from trpc_agent_sdk.evaluation import ScoreResult

        def custom(per_model, threshold, weights):
            return ScoreResult(score=1.0, reason="always pass")

        registry.register_models_aggregator("llm_final_response", custom)
        agg = registry.get_models_aggregator("llm_final_response")
        assert agg is not None
        out = agg.aggregate_models([ScoreResult(score=0.0)], 0.5, [1.0])
        assert out.score == 1.0

    def test_register_invalid_metric_raises(self, registry):
        """Test register_models_aggregator with non-LLM metric raises ValueError."""
        with pytest.raises(ValueError, match="must be one of"):
            registry.register_models_aggregator("rouge_score", lambda *a, **k: None)

    def test_get_unregistered_returns_none(self, registry):
        """Test get_models_aggregator returns None when not set."""
        assert registry.get_models_aggregator("llm_final_response") is None

    def test_unregister(self, registry):
        """Test unregister_models_aggregator removes the registration."""
        from trpc_agent_sdk.evaluation import ScoreResult

        def custom(per_model, threshold, weights):
            return ScoreResult(score=1.0)

        registry.register_models_aggregator("llm_rubric_response", custom)
        assert registry.get_models_aggregator("llm_rubric_response") is not None
        registry.unregister_models_aggregator("llm_rubric_response")
        assert registry.get_models_aggregator("llm_rubric_response") is None
