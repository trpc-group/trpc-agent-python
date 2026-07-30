"""Validated input and policy configuration contracts."""

from __future__ import annotations

from typing import Optional

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from trpc_agent_sdk.evaluation import EvalConfig, EvalSet, OptimizeConfig

from .models import FailureCategory, RunMode
from .schema import (
    StrictModel,
    finite_number as _finite,
    non_negative_number as _non_negative,
)


class AttributionConfig(StrictModel):
    metric_categories: dict[str, FailureCategory] = Field(default_factory=dict)
    rubric_categories: dict[str, FailureCategory] = Field(default_factory=dict)


class GateConfig(StrictModel):
    min_validation_score_delta: StrictFloat = 0.0
    min_validation_pass_rate_delta: StrictFloat = 0.0
    max_new_hard_failures: StrictInt = Field(default=0, ge=0)
    critical_case_min_delta: StrictFloat = 0.0
    metric_max_regression: dict[str, StrictFloat] = Field(default_factory=dict)
    max_cost_usd: Optional[StrictFloat] = None
    max_duration_seconds: Optional[StrictFloat] = 180.0
    epsilon: StrictFloat = 1e-9
    overfit_guard: StrictBool = True

    @field_validator(
        "min_validation_score_delta",
        "min_validation_pass_rate_delta",
        "critical_case_min_delta",
    )
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        return _finite(value, "gate threshold")

    @field_validator("epsilon")
    @classmethod
    def valid_epsilon(cls, value: float) -> float:
        return _non_negative(value, "epsilon")

    @field_validator("max_cost_usd", "max_duration_seconds")
    @classmethod
    def valid_budget(cls, value: Optional[float]) -> Optional[float]:
        return None if value is None else _non_negative(value, "budget")

    @field_validator("overfit_guard")
    @classmethod
    def overfit_guard_is_mandatory(cls, value: bool) -> bool:
        if not value:
            raise ValueError("overfit_guard is a mandatory safety invariant")
        return value

    @field_validator("metric_max_regression")
    @classmethod
    def valid_metric_regression(cls, value: dict[str, float]) -> dict[str, float]:
        for metric, limit in value.items():
            if not metric:
                raise ValueError("metric regression keys must be non-empty")
            _non_negative(limit, f"metric_max_regression[{metric!r}]")
        return value


class PipelineSettings(StrictModel):
    mode: RunMode = "fake"
    seed: StrictInt = 42
    inner_selection_ratio: StrictFloat = 0.34
    apply_candidate: StrictBool = False
    artifact_root: str = "artifacts"
    trace_fixture: str = "traces/trace_cases.json"
    run_id: Optional[str] = None
    prompt_paths: dict[str, str] = Field(default_factory=lambda: {"system": "prompts/system.md"})
    critical_case_ids: tuple[str, ...] = ()
    hard_case_ids: tuple[str, ...] = ()
    metric_weights: dict[str, StrictFloat] = Field(default_factory=dict)
    train_case_weights: dict[str, StrictFloat] = Field(default_factory=dict)
    validation_case_weights: dict[str, StrictFloat] = Field(default_factory=dict)
    max_text_chars: StrictInt = Field(default=4000, ge=64, le=100_000)
    max_audit_file_bytes: StrictInt = Field(default=25 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    live_agent_call_max_cost_usd: Optional[StrictFloat] = None
    live_metric_call_max_cost_usd: Optional[StrictFloat] = None
    optimizer_shutdown_timeout_seconds: StrictFloat = Field(default=10.0, ge=0.1, le=300.0)
    max_import_files: StrictInt = Field(default=256, ge=1, le=10_000)
    max_import_file_bytes: StrictInt = Field(default=5 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_import_total_bytes: StrictInt = Field(default=25 * 1024 * 1024, ge=1, le=500 * 1024 * 1024)
    attribution: AttributionConfig = Field(default_factory=AttributionConfig)
    gate: GateConfig = Field(default_factory=GateConfig)

    @model_validator(mode="after")
    def valid_import_budget(self) -> "PipelineSettings":
        if self.max_import_total_bytes < self.max_import_file_bytes:
            raise ValueError("max_import_total_bytes must be at least max_import_file_bytes")
        return self

    @model_validator(mode="after")
    def valid_live_cost_bounds(self) -> "PipelineSettings":
        bounds = (
            self.live_agent_call_max_cost_usd,
            self.live_metric_call_max_cost_usd,
        )
        if (bounds[0] is None) != (bounds[1] is None):
            raise ValueError("live Agent and metric call cost bounds must be configured together")
        for value in bounds:
            if value is not None:
                _non_negative(value, "live call cost bound")
        return self

    @field_validator("inner_selection_ratio")
    @classmethod
    def valid_ratio(cls, value: float) -> float:
        value = _finite(value, "inner_selection_ratio")
        if not 0 < value < 1:
            raise ValueError("inner_selection_ratio must be in (0, 1)")
        return value

    @field_validator("metric_weights", "train_case_weights", "validation_case_weights")
    @classmethod
    def positive_weights(cls, value: dict[str, float]) -> dict[str, float]:
        for key, weight in value.items():
            if not key:
                raise ValueError("weight keys must be non-empty")
            if _finite(weight, f"weight[{key!r}]") <= 0:
                raise ValueError("weights must be positive")
        return value

    @field_validator("prompt_paths")
    @classmethod
    def non_empty_prompts(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("at least one prompt path is required")
        return value


class PipelineConfig(StrictModel):
    evaluate: EvalConfig
    optimize: OptimizeConfig
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)


class ValidatedRunConfig(StrictModel):
    root_dir: str
    config_path: str
    train_path: str
    validation_path: str
    trace_fixture_path: Optional[str]
    artifact_root: str
    run_id: str
    config: PipelineConfig
    train: EvalSet
    validation: EvalSet
    input_hashes: dict[str, str]
    prompt_paths: dict[str, str]
    prompt_hashes: dict[str, str]
    adapter_identity: str
    reproducibility_paths: tuple[str, ...]
    reproducibility_issues: tuple[str, ...] = ()
