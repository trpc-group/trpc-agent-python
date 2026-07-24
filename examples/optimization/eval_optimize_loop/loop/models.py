#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Configuration and report models for the closed-loop example."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

_SAFE_ARTIFACT_LABEL = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
_WINDOWS_DEVICE_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _validate_artifact_label(
    value: str,
    *,
    subject: str,
    reserved: set[str] | None = None,
) -> str:
    if re.fullmatch(_SAFE_ARTIFACT_LABEL, value) is None:
        raise ValueError(f"{subject} must be a safe artifact label")
    folded = value.casefold()
    if folded in _WINDOWS_DEVICE_NAMES or folded in (reserved or set()):
        raise ValueError(f"{subject} is a reserved artifact label: {value!r}")
    return value


def _casefold_duplicates(values: list[str]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for value in values:
        groups.setdefault(value.casefold(), []).append(value)
    return sorted(" == ".join(group) for group in groups.values() if len(group) > 1)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateSource(_StrictModel):
    """A deterministic prompt proposal used by the offline reflection model."""

    candidate_id: str = Field(pattern=_SAFE_ARTIFACT_LABEL)
    path: Path

    @field_validator("candidate_id")
    @classmethod
    def _candidate_id_is_artifact_safe(cls, value: str) -> str:
        return _validate_artifact_label(
            value,
            subject="candidate_id",
            reserved={"baseline"},
        )


class PipelineSpec(_StrictModel):
    """All paths and run controls needed by :class:`EvalOptimizePipeline`."""

    manifest_path: Path
    optimizer_config: Path
    regression_metrics_config: Path
    gate_config: Path
    train_dataset: Path
    validation_dataset: Path
    target_prompts: dict[str, Path] = Field(min_length=1)
    candidate_sources: list[CandidateSource] = Field(min_length=1)
    output_dir: Path
    seed: int = 91
    bootstrap_samples: int = Field(default=2000, ge=100)
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    apply_if_accepted: bool = False

    @model_validator(mode="after")
    def _validate_artifact_labels(self) -> "PipelineSpec":
        candidate_ids = [source.candidate_id for source in self.candidate_sources]
        duplicate_ids = _casefold_duplicates(candidate_ids)
        if duplicate_ids:
            raise ValueError(f"candidate_ids must be case-insensitively unique: {duplicate_ids}")
        prompt_names = list(self.target_prompts)
        duplicate_prompt_names = _casefold_duplicates(prompt_names)
        if duplicate_prompt_names:
            raise ValueError("target prompt names must be case-insensitively unique: "
                             f"{duplicate_prompt_names}")
        for name in prompt_names:
            _validate_artifact_label(name, subject="target prompt name")
        return self

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        output_dir: str | Path | None = None,
    ) -> "PipelineSpec":
        """Load a manifest and resolve every relative path beside it."""
        manifest_path = Path(path).expanduser().resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = manifest_path.parent

        def _resolve(value: str | Path) -> Path:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            return candidate.resolve()

        if output_dir is None:
            configured_output = _resolve(payload.get("output_dir", "runs/latest"))
        else:
            configured_output = Path(output_dir).expanduser().resolve()
        payload.pop("output_dir", None)
        candidates = [
            CandidateSource(
                candidate_id=item["candidate_id"],
                path=_resolve(item["path"]),
            ) for item in payload.pop("candidate_sources")
        ]
        target_prompts = {name: _resolve(prompt_path) for name, prompt_path in payload.pop("target_prompts").items()}
        return cls(
            manifest_path=manifest_path,
            optimizer_config=_resolve(payload.pop("optimizer_config")),
            regression_metrics_config=_resolve(payload.pop("regression_metrics_config")),
            gate_config=_resolve(payload.pop("gate_config")),
            train_dataset=_resolve(payload.pop("train_dataset")),
            validation_dataset=_resolve(payload.pop("validation_dataset")),
            target_prompts=target_prompts,
            candidate_sources=candidates,
            output_dir=configured_output,
            **payload,
        )


class MetricOutcome(_StrictModel):
    metric_name: str
    score: Optional[float] = Field(default=None, allow_inf_nan=False)
    threshold: float = Field(allow_inf_nan=False)
    passed: bool
    reason: str = ""


class FailureReason(_StrictModel):
    category: str
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class TrajectoryStep(_StrictModel):
    kind: str
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CaseEvaluation(_StrictModel):
    case_id: str
    passed: bool
    score: float = Field(allow_inf_nan=False)
    key_case: bool = False
    hard_fail: bool = False
    metrics: list[MetricOutcome] = Field(default_factory=list)
    primary_failure: Optional[str] = None
    failure_reasons: list[FailureReason] = Field(default_factory=list)
    actual_response: str = ""
    expected_response: str = ""
    key_trajectory: list[TrajectoryStep] = Field(default_factory=list)


class SplitEvaluation(_StrictModel):
    split: Literal["train", "validation"]
    pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    average_score: float = Field(allow_inf_nan=False)
    cases: list[CaseEvaluation]


class BaselineEvaluation(_StrictModel):
    train: SplitEvaluation
    validation: SplitEvaluation


DeltaStatus = Literal[
    "newly_passed",
    "newly_failed",
    "score_improved",
    "score_regressed",
    "unchanged",
]


class CaseDelta(_StrictModel):
    case_id: str
    status: DeltaStatus
    baseline_passed: bool
    candidate_passed: bool
    baseline_score: float = Field(allow_inf_nan=False)
    candidate_score: float = Field(allow_inf_nan=False)
    score_delta: float = Field(allow_inf_nan=False)


class PairedConfidenceInterval(_StrictModel):
    point_estimate: float = Field(allow_inf_nan=False)
    lower: float = Field(allow_inf_nan=False)
    upper: float = Field(allow_inf_nan=False)
    confidence_level: float = Field(gt=0.0, lt=1.0, allow_inf_nan=False)
    bootstrap_samples: int = Field(ge=1)
    seed: int


class SplitDelta(_StrictModel):
    split: Literal["train", "validation"]
    pass_rate_delta: float = Field(allow_inf_nan=False)
    average_score_delta: float = Field(allow_inf_nan=False)
    paired_pass_rate_ci: PairedConfidenceInterval
    newly_passed: list[str] = Field(default_factory=list)
    newly_failed: list[str] = Field(default_factory=list)
    score_improved: list[str] = Field(default_factory=list)
    score_regressed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    cases: list[CaseDelta] = Field(default_factory=list)


class CandidateDelta(_StrictModel):
    train: SplitDelta
    validation: SplitDelta


class GateCheck(_StrictModel):
    name: str
    passed: bool
    actual: Any
    expected: Any
    reason: str
    required: bool = True


class GateDecision(_StrictModel):
    accepted: bool
    overfitting_detected: bool = False
    checks: list[GateCheck] = Field(default_factory=list)


class ResourceUsage(_StrictModel):
    metric_calls: int = Field(default=0, ge=0)
    reflection_calls: int = Field(default=0, ge=0)
    judge_calls: Optional[int] = Field(default=None, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: Optional[float] = Field(default=None, ge=0.0, allow_inf_nan=False)
    duration_seconds: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    p95_latency_ms: Optional[float] = Field(default=None, ge=0.0, allow_inf_nan=False)
    cost_measurement: str = "unavailable"


class CandidateAudit(_StrictModel):
    prompt_sha256: str
    source: str
    optimizer_round: Optional[int] = None
    seed: int
    resources: ResourceUsage


class CandidateEvaluation(_StrictModel):
    candidate_id: str
    prompts: dict[str, str]
    train: SplitEvaluation
    validation: SplitEvaluation
    delta: CandidateDelta
    gate: GateDecision
    audit: CandidateAudit
    pareto_optimal: bool = False


class OptimizerAudit(_StrictModel):
    algorithm: str
    status: str
    stop_reason: Optional[str] = None
    used_agent_optimizer: bool
    baseline_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    best_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    rounds: int = Field(ge=0)
    resources: ResourceUsage
    artifact_dir: str


class DataQualityAudit(_StrictModel):
    passed: bool
    train_cases: int = Field(ge=0)
    validation_cases: int = Field(ge=0)
    duplicate_ids: list[str] = Field(default_factory=list)
    cross_split_duplicates: list[str] = Field(default_factory=list)
    near_cross_split_duplicates: list[str] = Field(default_factory=list)
    prompt_leakage_matches: list[str] = Field(default_factory=list)


class RunAudit(_StrictModel):
    run_id: str
    started_at: str
    finished_at: str
    duration_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    seed: int
    config_sha256: str
    train_sha256: str
    validation_sha256: str
    baseline_prompt_sha256: dict[str, str]
    command: str


class FailureAttributionSummary(_StrictModel):
    explained_failed_cases: int = Field(ge=0)
    total_failed_cases: int = Field(ge=0)
    coverage_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    category_counts: dict[str, int] = Field(default_factory=dict)
    by_case: dict[str, list[FailureReason]] = Field(default_factory=dict)


class OptimizationReport(_StrictModel):
    schema_version: str = "1.0"
    status: Literal["accepted", "rejected", "failed"]
    baseline: BaselineEvaluation
    candidates: list[CandidateEvaluation]
    selected_candidate_id: Optional[str] = None
    candidate: Optional[CandidateEvaluation] = None
    delta: Optional[CandidateDelta] = None
    gate: Optional[GateDecision] = None
    failure_attribution: FailureAttributionSummary
    optimizer: OptimizerAudit
    data_quality: DataQualityAudit
    audit: RunAudit
