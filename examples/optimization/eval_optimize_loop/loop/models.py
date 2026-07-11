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
from pathlib import Path
from typing import Any
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateSource(_StrictModel):
    """A deterministic prompt proposal used by the offline reflection model."""

    candidate_id: str
    path: Path


class PipelineSpec(_StrictModel):
    """All paths and run controls needed by :class:`EvalOptimizePipeline`."""

    manifest_path: Path
    optimizer_config: Path
    regression_metrics_config: Path
    gate_config: Path
    train_dataset: Path
    validation_dataset: Path
    target_prompts: dict[str, Path]
    candidate_sources: list[CandidateSource]
    output_dir: Path
    seed: int = 91
    bootstrap_samples: int = Field(default=2000, ge=100)
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    apply_if_accepted: bool = False

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
    score: Optional[float] = None
    threshold: float
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
    score: float
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
    pass_rate: float
    average_score: float
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
    baseline_score: float
    candidate_score: float
    score_delta: float


class PairedConfidenceInterval(_StrictModel):
    point_estimate: float
    lower: float
    upper: float
    confidence_level: float
    bootstrap_samples: int
    seed: int


class SplitDelta(_StrictModel):
    split: Literal["train", "validation"]
    pass_rate_delta: float
    average_score_delta: float
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
    metric_calls: int = 0
    reflection_calls: int = 0
    judge_calls: Optional[int] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Optional[float] = None
    duration_seconds: float = 0.0
    p95_latency_ms: Optional[float] = None
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
    baseline_pass_rate: float
    best_pass_rate: float
    rounds: int
    resources: ResourceUsage
    artifact_dir: str


class DataQualityAudit(_StrictModel):
    passed: bool
    train_cases: int
    validation_cases: int
    duplicate_ids: list[str] = Field(default_factory=list)
    cross_split_duplicates: list[str] = Field(default_factory=list)
    near_cross_split_duplicates: list[str] = Field(default_factory=list)
    prompt_leakage_matches: list[str] = Field(default_factory=list)


class RunAudit(_StrictModel):
    run_id: str
    started_at: str
    finished_at: str
    duration_seconds: float
    seed: int
    config_sha256: str
    train_sha256: str
    validation_sha256: str
    baseline_prompt_sha256: dict[str, str]
    command: str


class FailureAttributionSummary(_StrictModel):
    explained_failed_cases: int
    total_failed_cases: int
    coverage_rate: float
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
