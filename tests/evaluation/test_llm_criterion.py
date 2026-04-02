# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for LLM criterion (_llm_criterion)."""

import pytest

pytest.importorskip("trpc_agent_sdk._runners", reason="trpc_agent_sdk._runners not yet implemented")

from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import DEFAULT_KNOWLEDGE_TOOL_NAMES
from trpc_agent_sdk.evaluation import DEFAULT_NUM_SAMPLES
from trpc_agent_sdk.evaluation import JudgeModelOptions
from trpc_agent_sdk.evaluation import LLMJudgeCriterion
from trpc_agent_sdk.evaluation import Rubric
from trpc_agent_sdk.evaluation import RubricContent
from trpc_agent_sdk.evaluation import get_llm_criterion_from_metric
from trpc_agent_sdk.evaluation import sanitize_criterion_for_export


class TestSanitizeCriterionForExport:
    """Test suite for sanitize_criterion_for_export."""

    def test_none_input(self):
        """Test None returns None."""
        assert sanitize_criterion_for_export(None) is None

    def test_non_dict_input(self):
        """Test non-dict returns as-is."""
        assert sanitize_criterion_for_export("x") == "x"

    def test_strips_api_key_llm_judge(self):
        """Test api_key is stripped from llm_judge.judge_model."""
        c = {
            "llm_judge": {
                "judge_model": {
                    "model_name": "glm-4",
                    "api_key": "secret",
                    "base_url": "http://x",
                },
            },
        }
        out = sanitize_criterion_for_export(c)
        assert out is not c
        judge = out["llm_judge"]["judge_model"]
        assert "api_key" not in judge
        assert "apiKey" not in judge
        assert judge["model_name"] == "glm-4"
        assert judge["base_url"] == "http://x"

    def test_strips_api_key_camel_case(self):
        """Test apiKey is stripped when key is judgeModel."""
        c = {
            "llmJudge": {
                "judgeModel": {"model_name": "glm-4", "apiKey": "secret"},
            },
        }
        out = sanitize_criterion_for_export(c)
        judge = out["llmJudge"]["judgeModel"]
        assert "apiKey" not in judge
        assert "api_key" not in judge


class TestRubric:
    """Test suite for Rubric model."""

    def test_rubric_minimal(self):
        """Test Rubric with id and content."""
        r = Rubric(
            id="1",
            content=RubricContent(text="Answer must include temperature."),
            description="Temperature",
            type="FINAL_RESPONSE_QUALITY",
        )
        assert r.id == "1"
        assert r.content.text == "Answer must include temperature."
        assert r.description == "Temperature"
        assert r.type == "FINAL_RESPONSE_QUALITY"


class TestJudgeModelOptions:
    """Test suite for JudgeModelOptions."""

    def test_get_num_samples_default(self):
        """Test default num_samples."""
        o = JudgeModelOptions(model_name="glm-4")
        assert o.get_num_samples() == DEFAULT_NUM_SAMPLES

    def test_get_num_samples_custom(self):
        """Test custom num_samples."""
        o = JudgeModelOptions(model_name="glm-4", num_samples=3)
        assert o.get_num_samples() == 3

    def test_serialize_omits_api_key(self):
        """Test that api_key is omitted in serialization."""
        o = JudgeModelOptions(model_name="glm-4", api_key="secret")
        d = o.model_dump()
        assert "api_key" not in d
        assert "apiKey" not in d


class TestLLMJudgeCriterion:
    """Test suite for LLMJudgeCriterion."""

    def test_from_dict_none(self):
        """Test from_dict with None."""
        assert LLMJudgeCriterion.from_dict(None) is None

    def test_from_dict_empty(self):
        """Test from_dict with empty dict returns None (falsy input)."""
        c = LLMJudgeCriterion.from_dict({})
        assert c is None

    def test_from_dict_snake_case(self):
        """Test from_dict with snake_case keys."""
        d = {
            "judge_model": {"model_name": "glm-4", "num_samples": 2},
            "rubrics": [
                {"id": "1", "content": {"text": "Must be relevant."}, "description": "Relevance"},
            ],
        }
        c = LLMJudgeCriterion.from_dict(d)
        assert c is not None
        assert c.judge_model is not None
        assert c.judge_model.model_name == "glm-4"
        assert c.get_num_samples() == 2
        assert len(c.rubrics) == 1
        assert c.rubrics[0].id == "1"
        assert c.rubrics[0].content.text == "Must be relevant."

    def test_get_knowledge_tool_names_default(self):
        """Test default knowledge tool names when knowledge_tool_names not set."""
        c = LLMJudgeCriterion.from_dict({"rubrics": []})  # minimal valid dict
        assert c is not None
        assert c.get_knowledge_tool_names() == DEFAULT_KNOWLEDGE_TOOL_NAMES

    def test_get_knowledge_tool_names_custom(self):
        """Test custom knowledge_tool_names."""
        c = LLMJudgeCriterion.from_dict({"knowledge_tool_names": ["retrieve", "search"]})
        assert c.get_knowledge_tool_names() == ["retrieve", "search"]


class TestGetLlmCriterionFromMetric:
    """Test suite for get_llm_criterion_from_metric."""

    def test_none_metric(self):
        """Test None metric returns None."""
        assert get_llm_criterion_from_metric(None) is None

    def test_metric_no_criterion(self):
        """Test metric without criterion returns None."""
        m = EvalMetric(metric_name="tool_trajectory_avg_score", threshold=0.8)
        assert get_llm_criterion_from_metric(m) is None

    def test_metric_with_llm_judge(self):
        """Test metric with llm_judge in criterion."""
        m = EvalMetric(
            metric_name="llm_final_response",
            threshold=1.0,
            criterion={
                "llm_judge": {
                    "judge_model": {"model_name": "glm-4"},
                },
            },
        )
        c = get_llm_criterion_from_metric(m)
        assert c is not None
        assert c.judge_model is not None
        assert c.judge_model.model_name == "glm-4"

    def test_metric_with_llm_judge_camel_case(self):
        """Test metric with llmJudge (camelCase) in criterion."""
        m = EvalMetric(
            metric_name="llm_rubric_response",
            threshold=1.0,
            criterion={
                "llmJudge": {
                    "judgeModel": {"model_name": "glm-4"},
                    "rubrics": [],
                },
            },
        )
        c = get_llm_criterion_from_metric(m)
        assert c is not None
        assert c.judge_model.model_name == "glm-4"
