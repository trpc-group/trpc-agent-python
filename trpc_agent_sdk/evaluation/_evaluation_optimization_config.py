# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration for the evaluation and optimization regression pipeline."""

from __future__ import annotations

from typing import Literal
from typing import Optional

from pydantic import Field
from pydantic import field_validator

from ._common import EvalBaseModel
from ._optimize_config import OptimizeConfigFile

FailureCategoryName = Literal[
    "final_response_mismatch",
    "tool_call_error",
    "tool_argument_error",
    "llm_rubric_failure",
    "knowledge_recall_failure",
    "format_violation",
    "execution_error",
    "unknown_failure",
]


class OptimizationGateConfig(EvalBaseModel):
    """Acceptance policy applied after candidates are re-evaluated."""

    min_validation_score_delta: float = Field(
        default=0.01,
        ge=-1.0,
        le=1.0,
        description="Minimum validation mean-score gain over baseline.",
    )
    min_validation_pass_rate_delta: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Minimum validation pass-rate gain over baseline.",
    )
    reject_new_hard_fail: bool = Field(
        default=True,
        description="Reject when a candidate introduces a configured hard failure.",
    )
    reject_overfitting: bool = Field(
        default=True,
        description=("Reject train-only gains that miss the validation threshold "
                     "or introduce a validation regression."),
    )
    critical_case_ids: list[str] = Field(
        default_factory=list,
        description="Case ids that must not lose score or transition from pass to fail.",
    )
    max_critical_score_drop: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Maximum tolerated score drop for a critical case.",
    )
    max_validation_regressions: Optional[int] = Field(
        default=None,
        ge=0,
        description="Optional cap on validation cases whose score or status regresses.",
    )
    max_total_cost_usd: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Optional total optimizer plus estimated evaluation cost budget.",
    )

    @field_validator("critical_case_ids")
    @classmethod
    def _unique_critical_case_ids(cls, value: list[str]) -> list[str]:
        if any(not case_id.strip() for case_id in value):
            raise ValueError("critical_case_ids cannot contain empty values")
        if len(value) != len(set(value)):
            raise ValueError("critical_case_ids cannot contain duplicates")
        return value


class OptimizationPipelineConfig(EvalBaseModel):
    """Evaluation and optimization regression-loop configuration."""

    mode: Literal["live", "fake", "trace"] = Field(
        default="live",
        description="Execution mode recorded in the audit report.",
    )
    report_language: Literal["en", "zh-CN"] = Field(
        default="en",
        description="Human-readable Markdown report language.",
    )
    gate: OptimizationGateConfig = Field(
        default_factory=OptimizationGateConfig,
        description="Final candidate acceptance policy.",
    )
    hard_fail_case_ids: list[str] = Field(
        default_factory=list,
        description="Failed cases that are always classified as hard failures.",
    )
    hard_fail_categories: list[FailureCategoryName] = Field(
        default_factory=lambda: [
            "tool_call_error",
            "tool_argument_error",
            "execution_error",
        ],
        description="Failure categories treated as hard failures.",
    )
    failure_category_overrides: dict[str, FailureCategoryName] = Field(
        default_factory=dict,
        description="Optional case-id to failure-category overrides for domain-specific failures.",
    )
    max_candidates: int = Field(
        default=10,
        ge=1,
        description="Maximum optimizer candidates independently regression-tested.",
    )
    evaluation_case_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated cost per evaluated case/run when providers do not expose billing.",
    )
    update_source: bool = Field(
        default=False,
        description="Write the selected prompt to its source only after the final gate accepts it.",
    )

    @field_validator("hard_fail_case_ids")
    @classmethod
    def _unique_hard_fail_case_ids(cls, value: list[str]) -> list[str]:
        if any(not case_id.strip() for case_id in value):
            raise ValueError("hard_fail_case_ids cannot contain empty values")
        if len(value) != len(set(value)):
            raise ValueError("hard_fail_case_ids cannot contain duplicates")
        return value

    @field_validator("hard_fail_categories")
    @classmethod
    def _unique_hard_fail_categories(
        cls,
        value: list[FailureCategoryName],
    ) -> list[FailureCategoryName]:
        if len(value) != len(set(value)):
            raise ValueError("hard_fail_categories cannot contain duplicates")
        return value


class EvaluationOptimizationConfigFile(OptimizeConfigFile):
    """Optimizer configuration extended with the final regression policy."""

    pipeline: OptimizationPipelineConfig = Field(
        default_factory=OptimizationPipelineConfig,
        description="Regression, acceptance, audit, and source-update policy.",
    )


def load_evaluation_optimization_config(path: str, ) -> EvaluationOptimizationConfigFile:
    """Load and validate a pipeline optimizer JSON configuration."""
    with open(path, "r", encoding="utf-8") as file:
        return EvaluationOptimizationConfigFile.model_validate_json(file.read())
