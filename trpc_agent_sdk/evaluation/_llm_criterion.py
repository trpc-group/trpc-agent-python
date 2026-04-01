# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Criterion types and helpers for LLM judge metrics (EvalMetric.criterion)."""

from __future__ import annotations

import copy
from typing import Any
from typing import Optional

from pydantic import Field
from pydantic import model_serializer

from ._common import EvalBaseModel

DEFAULT_NUM_SAMPLES = 1


def sanitize_criterion_for_export(criterion: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Return a copy of criterion with api_key removed from nested llmJudge/judgeModel.
    Use when exporting to JSON to avoid writing secrets."""
    if not criterion or not isinstance(criterion, dict):
        return criterion
    out = copy.deepcopy(criterion)
    for key in ("llmJudge", "llm_judge"):
        block = out.get(key)
        if not isinstance(block, dict):
            continue
        for jkey in ("judgeModel", "judge_model"):
            judge = block.get(jkey)
            if isinstance(judge, dict):
                judge = {k: v for k, v in judge.items() if k not in ("api_key", "apiKey")}
                block = {**block, jkey: judge}
        out = {**out, key: block}
    return out


DEFAULT_KNOWLEDGE_TOOL_NAMES = ["knowledge_search"]


class RubricContent(EvalBaseModel):
    """Content for one rubric item, shown to the judge model."""

    text: str = Field(default="", description="Text shown to the judge.")


class Rubric(EvalBaseModel):
    """One rubric item for LLM evaluation (id, content, description, type)."""

    id: str = Field(default="", description="Unique id for this rubric item.")
    content: Optional[RubricContent] = Field(
        default=None,
        description="Content shown to the judge model.",
    )
    description: str = Field(default="", description="Short human-readable description.")
    type: str = Field(default="", description="Rubric type label (e.g. FINAL_RESPONSE_QUALITY).")


class JudgeModelOptions(EvalBaseModel):
    """Judge model config: provider, model, num_samples, generation_config.
    api_key is omitted when serialized."""

    provider_name: str = Field(default="", description="LLM provider name.")
    model_name: str = Field(default="", description="Judge model name.")
    variant: str = Field(default="", description="OpenAI-compatible variant when provider is openai.")
    base_url: Optional[str] = Field(default=None, description="Optional custom endpoint.")
    api_key: str = Field(default="", description="API key for the judge provider.")
    extra_fields: Optional[dict[str, Any]] = Field(
        default=None,
        description="Extra provider-specific fields.",
    )
    num_samples: Optional[int] = Field(
        default=None,
        description="Number of judge samples per invocation; default DEFAULT_NUM_SAMPLES.",
    )
    generation_config: Optional[dict[str, Any]] = Field(
        default=None,
        description="Generation params: max_tokens, temperature, stream, etc.",
    )

    def get_num_samples(self) -> int:
        """Return configured num_samples or DEFAULT_NUM_SAMPLES."""
        if self.num_samples is not None and self.num_samples > 0:
            return self.num_samples
        return DEFAULT_NUM_SAMPLES

    @model_serializer(mode="wrap")
    def _serialize(self, serializer: Any) -> dict[str, Any]:
        """Omit api_key from serialization."""
        data = serializer(self)
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k not in ("api_key", "apiKey")}
        return data


class RubricScore(EvalBaseModel):
    """One rubric item's score from the judge (id, reason, score)."""

    id: str = Field(default="", description="Rubric item id.")
    reason: str = Field(default="", description="Reason for this score.")
    score: float = Field(default=0.0, description="Numeric score for this rubric item.")


class ScoreResult(EvalBaseModel):
    """Result of one judge call: score, reason, and optional per-rubric rubric_scores."""

    score: float = Field(default=0.0, description="Numeric score.")
    reason: str = Field(default="", description="Reason from judge.")
    rubric_scores: list[RubricScore] = Field(
        default_factory=list,
        description="Per-rubric scores for rubric-based metrics.",
    )


class LLMJudgeCriterion(EvalBaseModel):
    """Criterion for LLM judge metrics: judge_model, rubrics, knowledge_tool_names.
    Built from EvalMetric.criterion.llmJudge. Use LLM evaluators to run the judge."""

    judge_model: Optional[JudgeModelOptions] = Field(
        default=None,
        description="Judge model options (required for all LLM judge metrics).",
    )
    rubrics: list[Rubric] = Field(
        default_factory=list,
        description="Rubric items for rubric-based metrics.",
    )
    knowledge_tool_names: Optional[list[str]] = Field(
        default=None,
        description=("Tool names treated as knowledge retrieval for llm_rubric_knowledge_recall. "
                     "If unset, DEFAULT_KNOWLEDGE_TOOL_NAMES is used."),
    )

    def get_num_samples(self) -> int:
        """Return judge_model num_samples or DEFAULT_NUM_SAMPLES."""
        if self.judge_model is not None:
            return self.judge_model.get_num_samples()
        return DEFAULT_NUM_SAMPLES

    def get_knowledge_tool_names(self) -> list[str]:
        """Return knowledge tool names; uses DEFAULT_KNOWLEDGE_TOOL_NAMES when unset."""
        if self.knowledge_tool_names:
            return list(self.knowledge_tool_names)
        return list(DEFAULT_KNOWLEDGE_TOOL_NAMES)

    @classmethod
    def from_dict(cls, d: dict | None) -> Optional["LLMJudgeCriterion"]:
        """Build from config dict (judgeModel, rubrics, knowledge_tool_names; camelCase or snake_case).
        Returns None if d is None or validation fails."""
        if not d or not isinstance(d, dict):
            return None
        try:
            return cls.model_validate(d)
        except Exception:
            return None


def get_llm_criterion_from_metric(eval_metric: Any) -> Optional[LLMJudgeCriterion]:
    """Return LLMJudgeCriterion from EvalMetric.criterion when llmJudge config is present.
    For criterion registry use CRITERION_REGISTRY.build(criterion, metric_key=...) instead."""
    if eval_metric is None or not getattr(eval_metric, "criterion", None):
        return None
    c = getattr(eval_metric, "criterion")
    if not isinstance(c, dict):
        return None
    llm_raw = c.get("llmJudge") or c.get("llm_judge")
    if not isinstance(llm_raw, dict):
        return None
    return LLMJudgeCriterion.from_dict(llm_raw)
