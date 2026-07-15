# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Pipeline-specific configuration.

``optimizer.json`` deliberately remains an SDK ``OptimizeConfigFile``.  The
configuration in this module contains only orchestration concerns that do not
belong in the SDK optimizer schema: isolated prompt sources, gate policy,
budgets, reporting, and artifact retention.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from typing import Optional
from typing import Union

from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from trpc_agent_sdk.evaluation import EvalBaseModel


_PROMPT_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*$"
_RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


class ExecutionConfig(EvalBaseModel):
    """How a later phase obtains an optimization candidate."""

    mode: Literal["fake", "real", "trace"] = "fake"
    fake_candidate_scenario: Literal["improve", "no_improvement", "overfit"] = "improve"
    use_fake_judge: bool = False


class InputPathsConfig(EvalBaseModel):
    """Files shared by the baseline, candidate, and optimizer runs."""

    train_evalset: str
    validation_evalset: str
    optimizer_config: str

    @field_validator("train_evalset", "validation_evalset", "optimizer_config")
    @classmethod
    def _require_non_empty_relative_path(cls, value: str) -> str:
        path = Path(value)
        if not value.strip():
            raise ValueError("path must not be empty")
        if path.is_absolute():
            raise ValueError("path must be relative to pipeline.json")
        return value


class PromptFieldConfig(EvalBaseModel):
    """One file-backed field that forms the pipeline TargetPrompt."""

    name: str = Field(pattern=_PROMPT_NAME_PATTERN)
    path: str

    @field_validator("path")
    @classmethod
    def _require_non_empty_relative_path(cls, value: str) -> str:
        path = Path(value)
        if not value.strip():
            raise ValueError("prompt path must not be empty")
        if path.is_absolute():
            raise ValueError("prompt path must be relative to pipeline.json")
        return value


class RunConfig(EvalBaseModel):
    """Reproducibility and workspace location settings."""

    runs_dir: str = "runs"
    run_id: Optional[str] = Field(default=None, pattern=_RUN_ID_PATTERN)
    seed: int = 42

    @field_validator("runs_dir")
    @classmethod
    def _require_non_empty_relative_path(cls, value: str) -> str:
        path = Path(value)
        if not value.strip():
            raise ValueError("runs_dir must not be empty")
        if path.is_absolute():
            raise ValueError("runs_dir must be relative to pipeline.json")
        return value


class CaseLabelsConfig(EvalBaseModel):
    """Case identifiers with stronger gate guarantees."""

    hard_case_ids: list[str] = Field(default_factory=list)
    critical_case_ids: list[str] = Field(default_factory=list)

    @field_validator("hard_case_ids", "critical_case_ids")
    @classmethod
    def _require_unique_non_empty_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("case labels must not contain empty IDs")
        if len(values) != len(set(values)):
            raise ValueError("case labels must not contain duplicate IDs")
        return values


class GateConfig(EvalBaseModel):
    """Acceptance policy consumed by the gate phase."""

    min_validation_score_delta: float = Field(default=0.01, ge=0.0)
    reject_on_validation_pass_rate_drop: bool = True
    reject_new_hard_fail: bool = True
    reject_critical_regression: bool = True
    severe_case_score_drop: float = Field(default=0.20, ge=0.0, le=1.0)
    required_metrics: Union[Literal["all"], list[str]] = "all"

    @field_validator("required_metrics")
    @classmethod
    def _require_unique_metric_names(cls, value: Union[str, list[str]]) -> Union[str, list[str]]:
        if not isinstance(value, list):
            return value
        if any(not item.strip() for item in value):
            raise ValueError("required_metrics must not contain empty metric names")
        if len(value) != len(set(value)):
            raise ValueError("required_metrics must not contain duplicates")
        return value


class BudgetConfig(EvalBaseModel):
    """Resource limits and the policy for measurements unavailable from the SDK."""

    max_cost_usd: Optional[float] = Field(default=None, ge=0.0)
    max_tokens: Optional[int] = Field(default=None, ge=0)
    max_duration_seconds: Optional[float] = Field(default=None, gt=0.0)
    on_unavailable: Literal["reject", "warning"] = "reject"


class ReportingConfig(EvalBaseModel):
    """Report formats selected for a successful future pipeline run."""

    write_json: bool = True
    write_markdown: bool = True
    include_case_evidence: bool = True

    @model_validator(mode="after")
    def _require_report_format(self) -> "ReportingConfig":
        if not self.write_json and not self.write_markdown:
            raise ValueError("at least one of write_json or write_markdown must be enabled")
        return self


class ArtifactConfig(EvalBaseModel):
    """Which reproducibility artifacts future phases must retain."""

    copy_input_files: bool = True
    retain_optimizer_native_artifacts: bool = True
    audit_all_candidates: bool = False


class WritebackConfig(EvalBaseModel):
    """Safety settings used only after a future ACCEPT decision."""

    enabled: bool = False
    require_source_hash_match: bool = True


class PipelineConfig(EvalBaseModel):
    """The complete, example-local ``pipeline.json`` schema (version 1)."""

    config_version: Literal[1] = 1
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    inputs: InputPathsConfig
    prompts: list[PromptFieldConfig] = Field(min_length=1)
    run: RunConfig = Field(default_factory=RunConfig)
    case_labels: CaseLabelsConfig = Field(default_factory=CaseLabelsConfig)
    gate: GateConfig = Field(default_factory=GateConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    artifacts: ArtifactConfig = Field(default_factory=ArtifactConfig)
    writeback: WritebackConfig = Field(default_factory=WritebackConfig)

    @model_validator(mode="after")
    def _require_unique_prompt_names(self) -> "PipelineConfig":
        names = [prompt.name for prompt in self.prompts]
        if len(names) != len(set(names)):
            raise ValueError("prompts must not contain duplicate field names")
        return self


def load_pipeline_config(path: str | Path) -> PipelineConfig:
    """Load a pipeline config while retaining path resolution at the caller.

    Paths intentionally remain relative strings in the model so a copied example
    directory remains relocatable.  ``prepare_run`` resolves and validates them
    relative to this file.
    """
    config_path = Path(path)
    return PipelineConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
