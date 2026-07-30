# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LLM criterion (_llm_criterion)."""

import trpc_agent_sdk.runners  # noqa: F401

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
                "judgeModel": {
                    "model_name": "glm-4",
                    "apiKey": "secret"
                },
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
            "judge_model": {
                "model_name": "glm-4",
                "num_samples": 2
            },
            "rubrics": [
                {
                    "id": "1",
                    "content": {
                        "text": "Must be relevant."
                    },
                    "description": "Relevance"
                },
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
                    "judge_model": {
                        "model_name": "glm-4"
                    },
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
                    "judgeModel": {
                        "model_name": "glm-4"
                    },
                    "rubrics": [],
                },
            },
        )
        c = get_llm_criterion_from_metric(m)
        assert c is not None
        assert c.judge_model.model_name == "glm-4"


class TestJudgeModelOptionsWeight:
    """Test suite for JudgeModelOptions.weight."""

    def test_weight_default_is_one(self):
        """Test weight defaults to 1.0 when omitted."""
        opts = JudgeModelOptions(model_name="m")
        assert opts.weight == 1.0

    def test_weight_custom_value(self):
        """Test weight accepts custom float."""
        opts = JudgeModelOptions(model_name="m", weight=2.5)
        assert opts.weight == 2.5


class TestLLMJudgeCriterionMultiModel:
    """Test suite for LLMJudgeCriterion multi-model fields and validation."""

    def test_default_models_aggregator_and_parallel(self):
        """Test defaults: models_aggregator='all_pass', parallel=True."""
        c = LLMJudgeCriterion(judge_model=JudgeModelOptions(model_name="m"))
        assert c.models_aggregator == "all_pass"
        assert c.parallel is True

    def test_get_judge_models_normalizes_singular(self):
        """Test get_judge_models() returns 1-element list when only judge_model is set."""
        c = LLMJudgeCriterion(judge_model=JudgeModelOptions(model_name="m1"))
        models = c.get_judge_models()
        assert len(models) == 1
        assert models[0].model_name == "m1"

    def test_get_judge_models_returns_list_directly(self):
        """Test get_judge_models() returns judge_models when set."""
        c = LLMJudgeCriterion(judge_models=[
            JudgeModelOptions(model_name="m1"),
            JudgeModelOptions(model_name="m2"),
        ], )
        models = c.get_judge_models()
        assert [m.model_name for m in models] == ["m1", "m2"]

    def test_get_judge_models_empty_when_neither_set(self):
        """Test get_judge_models() returns [] when neither field set (allowed at criterion level)."""
        c = LLMJudgeCriterion()
        assert c.get_judge_models() == []

    def test_validate_judge_model_and_judge_models_mutually_exclusive(self):
        """Test setting both judge_model and judge_models raises ValueError."""
        import pytest as _pytest
        with _pytest.raises(ValueError, match="judge_model.*judge_models"):
            LLMJudgeCriterion(
                judge_model=JudgeModelOptions(model_name="m1"),
                judge_models=[JudgeModelOptions(model_name="m2")],
            )

    def test_validate_empty_judge_models_raises(self):
        """Test empty judge_models list raises ValueError."""
        import pytest as _pytest
        with _pytest.raises(ValueError, match="judge_models.*empty"):
            LLMJudgeCriterion(judge_models=[])

    def test_validate_negative_weight_raises(self):
        """Test any negative weight raises ValueError."""
        import pytest as _pytest
        with _pytest.raises(ValueError, match="weight.*negative"):
            LLMJudgeCriterion(judge_models=[
                JudgeModelOptions(model_name="m1", weight=1.0),
                JudgeModelOptions(model_name="m2", weight=-0.5),
            ], )

    def test_validate_weighted_aggregator_zero_total_weight_raises(self):
        """Test weighted_avg with all-zero weights raises ValueError."""
        import pytest as _pytest
        with _pytest.raises(ValueError, match="weight"):
            LLMJudgeCriterion(
                judge_models=[
                    JudgeModelOptions(model_name="m1", weight=0.0),
                    JudgeModelOptions(model_name="m2", weight=0.0),
                ],
                models_aggregator="weighted_avg",
            )

    def test_built_in_aggregator_names_accepted(self):
        """Test all 6 built-in aggregator names pass validation."""
        for name in ("all_pass", "any_pass", "majority_pass", "avg", "weighted_avg", "weighted_majority"):
            c = LLMJudgeCriterion(
                judge_models=[JudgeModelOptions(model_name="m", weight=1.0)],
                models_aggregator=name,
            )
            assert c.models_aggregator == name

    def test_validate_models_aggregator_must_be_non_empty_string(self):
        """Test empty models_aggregator string raises ValueError at criterion level."""
        import pytest as _pytest
        with _pytest.raises(ValueError, match="models_aggregator.*non-empty"):
            LLMJudgeCriterion(
                judge_model=JudgeModelOptions(model_name="m"),
                models_aggregator="",
            )

    def test_from_dict_with_judge_models(self):
        """Test from_dict accepts judge_models list and models_aggregator string."""
        c = LLMJudgeCriterion.from_dict({
            "judge_models": [
                {
                    "model_name": "m1",
                    "weight": 2.0
                },
                {
                    "model_name": "m2",
                    "weight": 1.0
                },
            ],
            "models_aggregator":
            "weighted_avg",
            "parallel":
            False,
        })
        assert c is not None
        assert len(c.judge_models) == 2
        assert c.judge_models[0].weight == 2.0
        assert c.models_aggregator == "weighted_avg"
        assert c.parallel is False

    def test_from_dict_legacy_judge_model_still_works(self):
        """Test from_dict still works with legacy single judge_model (back compat)."""
        c = LLMJudgeCriterion.from_dict({
            "judge_model": {
                "model_name": "glm-4"
            },
        })
        assert c is not None
        assert c.judge_model.model_name == "glm-4"
        assert c.judge_models is None
        assert c.models_aggregator == "all_pass"
        assert c.parallel is True
