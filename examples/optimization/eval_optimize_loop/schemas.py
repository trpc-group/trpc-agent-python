# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Serializable data schemas owned by the pipeline example.

The SDK evaluation result types remain the source of truth for raw evaluation
data. These schemas capture run inputs, prompt provenance, fake candidates,
and the full stage-two evaluation outputs consumed by later pipeline phases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Generic
from typing import Literal
from typing import Optional
from typing import TypeVar
from typing import Union

from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from trpc_agent_sdk.evaluation import EvalBaseModel
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import OptimizeResult


FakeCandidateScenario = Literal["improve", "no_improvement", "overfit"]
EvaluationStatus = Literal["passed", "failed", "not_evaluated"]
FailureCategory = Literal[
    "evaluation_error",
    "tool_name_error",
    "tool_argument_error",
    "knowledge_recall",
    "format_error",
    "rubric_failure",
    "routing_error",
    "final_response_mismatch",
    "unknown",
]
ChangeKind = Literal[
    "newly_passed",
    "newly_failed",
    "improved",
    "regressed",
    "unchanged",
    "incomparable",
]
OverfitStatus = Literal["detected", "not_detected", "unavailable"]
GateRuleId = Literal[
    "evaluation_completeness",
    "minimum_validation_score_delta",
    "validation_pass_rate_non_decrease",
    "no_new_hard_fail",
    "no_critical_regression",
    "no_severe_regression",
    "required_metrics",
    "no_overfitting",
    "cost_budget",
    "token_budget",
    "duration_budget",
]
GateRuleOutcome = Literal["pass", "reject", "warning", "skipped"]
GateDecisionValue = Literal["accept", "reject"]
WritebackStatus = Literal["skipped", "written", "blocked", "failed"]
WritebackReason = Literal[
    "gate_rejected",
    "disabled",
    "source_drift",
    "write_error",
    "readback_mismatch",
    "written",
]


class OptimizerRuntimeParameters(EvalBaseModel):
    """命令行显式传入的反思优化模型参数，不包含任何凭据。"""

    provider_name: str = "openai"
    model_name: str
    variant: str = ""
    temperature: float = Field(default=0.8, ge=0.0, allow_inf_nan=False)
    max_tokens: int = Field(default=4096, gt=0)
    think: Optional[bool] = None
    max_candidate_proposals: int = Field(default=1, gt=0)

    @field_validator("provider_name", "model_name")
    @classmethod
    def _require_non_empty_model_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model identity must not be empty")
        return value.strip()


class ObservableValue(EvalBaseModel):
    """A measurement whose absence is explicit rather than silently zero."""

    status: Literal["available", "unavailable"]
    value: Optional[float] = None
    unit: Optional[str] = None
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _validate_status(self) -> "ObservableValue":
        if self.status == "available" and self.value is None:
            raise ValueError("available observable values require value")
        if self.status == "unavailable" and self.value is not None:
            raise ValueError("unavailable observable values must not carry a value")
        return self


class PromptSnapshot(EvalBaseModel):
    """Content and provenance of one source prompt field at preparation time."""

    field_name: str
    source_path: str
    working_path: str
    content: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InputSnapshot(EvalBaseModel):
    """Immutable file identities captured before a pipeline run starts."""

    pipeline_config_path: str
    pipeline_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    optimizer_config_path: str
    optimizer_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    train_evalset_path: str
    train_evalset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_evalset_path: str
    validation_evalset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_snapshots: list[PromptSnapshot]
    seed: int


class WorkspaceSnapshot(EvalBaseModel):
    """Directory layout created for one isolated pipeline run."""

    run_id: str
    run_dir: str
    workspace_dir: str
    prompts_dir: str


class CandidateProposal(EvalBaseModel):
    """Common, serializable identity and prompt payload for any provider."""

    provider: Literal["fake", "agent_optimizer"]
    prompts: dict[str, str]
    changed_fields: list[str]
    rationale: str
    parent_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str


class FakeCandidateProposal(CandidateProposal):
    """One deterministic prompt proposal produced without a real optimizer."""

    provider: Literal["fake"] = "fake"
    scenario: FakeCandidateScenario
    seed: int
    candidate_id: str = Field(pattern=r"^fake-(improve|no_improvement|overfit)-[0-9a-f]{12}$")


class OptimizerCandidateProposal(CandidateProposal):
    """Best candidate returned by a successful real AgentOptimizer run."""

    provider: Literal["agent_optimizer"] = "agent_optimizer"
    optimizer_status: Literal["SUCCEEDED"] = "SUCCEEDED"
    finish_reason: str
    stop_reason: Optional[str] = None
    baseline_pass_rate: float = Field(ge=0.0, le=1.0)
    best_pass_rate: float = Field(ge=0.0, le=1.0)
    optimizer_output_dir: Optional[str] = None
    candidate_id: str = Field(pattern=r"^optimizer-[0-9a-f]{12}$")


class FakeEvaluationSnapshot(EvalBaseModel):
    """Complete SDK outputs from one fake-agent dataset evaluation."""

    phase: Literal["baseline", "candidate"]
    split: Literal["train", "validation"]
    eval_set_id: str
    failed_summary: Optional[dict[str, Any]] = None
    details_lines: list[str] = Field(
        description=(
            "SDK detailed-output lines; intentionally empty in stage two because "
            "print_detailed_results is disabled."
        )
    )
    result_lines: list[str]
    eval_results_by_eval_id: dict[str, list[EvalCaseResult]]
    passed_case_count: int = Field(ge=0)
    total_case_count: int = Field(ge=0)
    average_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Arithmetic mean of every available overall metric score across "
            "all cases, configured runs, and metrics."
        ),
    )


class ToolCallEvidence(EvalBaseModel):
    """A compact tool call retained for attribution and reporting."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MetricOutcome(EvalBaseModel):
    """One normalized metric outcome for a run or an aggregate."""

    metric_name: str
    threshold: float
    status: EvaluationStatus
    score: ObservableValue
    reason: Optional[str] = None


class InvocationEvidence(EvalBaseModel):
    """Expected and actual evidence from one evaluated invocation."""

    invocation_id: str
    user_text: str
    expected_response: Optional[str] = None
    actual_response: Optional[str] = None
    expected_tools: list[ToolCallEvidence] = Field(default_factory=list)
    actual_tools: list[ToolCallEvidence] = Field(default_factory=list)
    metrics: list[MetricOutcome] = Field(default_factory=list)


class CaseRunOutcome(EvalBaseModel):
    """Normalized evidence from one configured run of an eval case."""

    run_id: int
    status: EvaluationStatus
    error_message: Optional[str] = None
    metrics: list[MetricOutcome]
    invocations: list[InvocationEvidence]


class AttributionEvidence(EvalBaseModel):
    """One concrete observation supporting a failure attribution."""

    evidence_type: Literal["execution_error", "metric", "response", "tool"]
    message: str
    run_id: Optional[int] = None
    invocation_id: Optional[str] = None
    metric_name: Optional[str] = None
    expected: Optional[Any] = None
    actual: Optional[Any] = None


class FailureAttribution(EvalBaseModel):
    """Deterministic primary and secondary reasons for a failed case."""

    primary_category: FailureCategory
    secondary_categories: list[FailureCategory] = Field(default_factory=list)
    summary: str
    evidence: list[AttributionEvidence] = Field(default_factory=list)


class CaseEvaluation(EvalBaseModel):
    """One eval case aggregated across all configured runs."""

    eval_id: str
    status: EvaluationStatus
    average_score: ObservableValue
    metrics: list[MetricOutcome]
    runs: list[CaseRunOutcome]
    attribution: Optional[FailureAttribution] = None


class StandardizedEvaluation(EvalBaseModel):
    """Stable case-oriented representation of one SDK evaluation snapshot."""

    phase: Literal["baseline", "candidate"]
    split: Literal["train", "validation"]
    eval_set_id: str
    cases: list[CaseEvaluation]
    passed_case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    not_evaluated_case_count: int = Field(ge=0)
    average_score: ObservableValue


class MetricDelta(EvalBaseModel):
    """Before/after comparison for one metric."""

    metric_name: str
    baseline_status: EvaluationStatus
    candidate_status: EvaluationStatus
    baseline_score: ObservableValue
    candidate_score: ObservableValue
    score_delta: ObservableValue
    change: ChangeKind


class CaseDiff(EvalBaseModel):
    """Before/after comparison and policy labels for one eval case."""

    eval_id: str
    split: Literal["train", "validation"]
    baseline_status: EvaluationStatus
    candidate_status: EvaluationStatus
    baseline_score: ObservableValue
    candidate_score: ObservableValue
    score_delta: ObservableValue
    change: ChangeKind
    metrics: list[MetricDelta]
    baseline_attribution: Optional[FailureAttribution] = None
    candidate_attribution: Optional[FailureAttribution] = None
    is_hard: bool = False
    is_critical: bool = False
    severe_regression: bool = False


class DatasetDiff(EvalBaseModel):
    """Case-level changes and aggregate deltas for one dataset split."""

    split: Literal["train", "validation"]
    eval_set_id: str
    cases: list[CaseDiff]
    baseline_average_score: ObservableValue
    candidate_average_score: ObservableValue
    score_delta: ObservableValue
    newly_passed_count: int = Field(ge=0)
    newly_failed_count: int = Field(ge=0)
    improved_count: int = Field(ge=0)
    regressed_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    incomparable_count: int = Field(ge=0)


class EvaluationAnalysis(EvalBaseModel):
    """All normalized evidence and comparisons produced by stage 3a."""

    baseline_train: StandardizedEvaluation
    baseline_validation: StandardizedEvaluation
    candidate_train: StandardizedEvaluation
    candidate_validation: StandardizedEvaluation
    train_diff: DatasetDiff
    validation_diff: DatasetDiff
    overfit_status: OverfitStatus
    overfit_reason: str


class ResourceMeasurements(EvalBaseModel):
    """Resource observations available when Gate evaluates a candidate."""

    cost_usd: ObservableValue
    total_tokens: ObservableValue
    duration_seconds: ObservableValue


class GateRuleResult(EvalBaseModel):
    """One deterministic policy result with evidence for later reporting."""

    rule_id: GateRuleId
    outcome: GateRuleOutcome
    message: str
    case_ids: list[str] = Field(default_factory=list)
    metric_names: list[str] = Field(default_factory=list)
    observed: dict[str, ObservableValue] = Field(default_factory=dict)
    threshold: Optional[float] = None


class GateDecision(EvalBaseModel):
    """The complete, auditable acceptance decision produced by Gate."""

    decision: GateDecisionValue
    rule_results: list[GateRuleResult]
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WritebackResult(EvalBaseModel):
    """Auditable outcome of the post-Gate source prompt operation."""

    status: WritebackStatus
    reason: WritebackReason
    attempted: bool = False
    changed_fields: list[str] = Field(default_factory=list)
    source_hashes_before: dict[str, str] = Field(default_factory=dict)
    source_hashes_after: dict[str, str] = Field(default_factory=dict)
    error_message: Optional[str] = None


class PipelineStageResult(EvalBaseModel):
    """Evaluation, analysis, Gate, and writeback fields shared by all modes."""

    baseline_train: FakeEvaluationSnapshot
    baseline_validation: FakeEvaluationSnapshot
    candidate_train: FakeEvaluationSnapshot
    candidate_validation: FakeEvaluationSnapshot
    analysis: EvaluationAnalysis
    measurements: ResourceMeasurements
    gate_decision: GateDecision
    writeback: WritebackResult


class FakeStageResult(PipelineStageResult):
    """Full deterministic fake-mode pipeline result."""

    scenario: FakeCandidateScenario
    candidate: FakeCandidateProposal


class RealStageResult(PipelineStageResult):
    """Full regression and Gate result for an AgentOptimizer proposal."""

    candidate: OptimizerCandidateProposal
    optimize_result: OptimizeResult

ReportPhase = Literal[
    "baseline_train", "baseline_validation", "candidate_generation", "candidate_train",
    "candidate_validation", "analysis", "gate", "writeback", "reporting",
]

class ReportProgress(EvalBaseModel):
    started_at: datetime
    current_phase: ReportPhase
    completed_phases: list[ReportPhase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_phases(self) -> "ReportProgress":
        if len(self.completed_phases) != len(set(self.completed_phases)):
            raise ValueError("completed phases must not contain duplicates")
        if self.current_phase in self.completed_phases:
            raise ValueError("completed phases must not include current phase")
        return self


OptimizerResourceValueT = TypeVar("OptimizerResourceValueT")


class OptimizerResourceValue(EvalBaseModel, Generic[OptimizerResourceValueT]):
    status: Literal["available", "unavailable", "not_applicable"]
    value: Optional[OptimizerResourceValueT] = None
    unit: str = Field(min_length=1)
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _validate_status(self) -> "OptimizerResourceValue[OptimizerResourceValueT]":
        if self.status == "available":
            if self.value is None:
                raise ValueError("available optimizer resource values require a value")
            if isinstance(self.value, (int, float)) and not self.value >= 0:
                raise ValueError("optimizer numeric resource values must be non-negative")
        else:
            if self.value is not None:
                raise ValueError("non-available optimizer resource values must not carry a value")
            if self.reason is None or not self.reason.strip():
                raise ValueError("non-available optimizer resource values require a reason")
        return self


class OptimizerResourceObservation(EvalBaseModel):
    scope_note: str
    total_rounds: OptimizerResourceValue[int]
    reflection_lm_calls: OptimizerResourceValue[int]
    cost_usd: OptimizerResourceValue[float]
    token_usage: OptimizerResourceValue[dict[str, int]]
    duration_seconds: OptimizerResourceValue[float]

class ArtifactReference(EvalBaseModel):
    artifact_id: str
    artifact_type: Literal["input", "prompt", "evaluation", "candidate", "optimizer_native", "report"]
    relative_path: Optional[str] = None
    required: bool
    produced_by: ReportPhase
    status: Literal["available", "unavailable"]
    size_bytes: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    unavailable_reason: Optional[str] = None

    @model_validator(mode="after")
    def _validate_status(self) -> "ArtifactReference":
        if self.status == "available":
            if self.relative_path is None or self.size_bytes is None or self.sha256 is None or self.unavailable_reason is not None:
                raise ValueError("available artifacts require path, size, hash, and no unavailable reason")
        elif self.unavailable_reason is None or self.size_bytes is not None or self.sha256 is not None:
            raise ValueError("unavailable artifacts require a reason and no size or hash")
        return self

class ArtifactIndex(EvalBaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    generated_at: datetime
    artifacts: list[ArtifactReference]

    @model_validator(mode="after")
    def _validate_artifacts(self) -> "ArtifactIndex":
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        paths = [artifact.relative_path for artifact in self.artifacts if artifact.relative_path is not None]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact IDs must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("artifact relative paths must be unique")
        return self

class OptimizationReport(EvalBaseModel):
    schema_version: Literal[1] = 1
    status: Literal["completed"] = "completed"
    run_id: str
    execution_mode: Literal["fake", "real"]
    seed: int
    started_at: datetime
    finished_at: datetime
    input_snapshot: InputSnapshot
    candidate: Union[FakeCandidateProposal, OptimizerCandidateProposal]
    baseline_train: FakeEvaluationSnapshot
    baseline_validation: FakeEvaluationSnapshot
    candidate_train: FakeEvaluationSnapshot
    candidate_validation: FakeEvaluationSnapshot
    analysis: EvaluationAnalysis
    pipeline_resources: ResourceMeasurements
    optimizer_resources: OptimizerResourceObservation
    gate_decision: GateDecision
    writeback: WritebackResult

class FailureReport(EvalBaseModel):
    schema_version: Literal[1] = 1
    status: Literal["failed"] = "failed"
    run_id: str
    execution_mode: Literal["fake", "real"]
    failed_phase: ReportPhase
    exception_type: str
    error_message: str
    generated_at: datetime
    input_snapshot: InputSnapshot
    source_prompt_hashes: dict[str, str]
    completed_phases: list[ReportPhase]
    existing_artifacts: list[str]
