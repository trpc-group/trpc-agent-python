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
