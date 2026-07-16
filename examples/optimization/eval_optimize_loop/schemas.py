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

from typing import Any
from typing import Literal
from typing import Optional

from pydantic import Field
from pydantic import model_validator

from trpc_agent_sdk.evaluation import EvalBaseModel
from trpc_agent_sdk.evaluation import EvalCaseResult


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


class FakeCandidateProposal(EvalBaseModel):
    """One deterministic prompt proposal produced without a real optimizer."""

    scenario: FakeCandidateScenario
    prompts: dict[str, str]
    changed_fields: list[str]
    rationale: str
    seed: int
    parent_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str = Field(pattern=r"^fake-(improve|no_improvement|overfit)-[0-9a-f]{12}$")


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


class FakeStageResult(EvalBaseModel):
    """Evaluation, candidate, analysis, and Gate outputs from the offline stage."""

    scenario: FakeCandidateScenario
    candidate: FakeCandidateProposal
    baseline_train: FakeEvaluationSnapshot
    baseline_validation: FakeEvaluationSnapshot
    candidate_train: FakeEvaluationSnapshot
    candidate_validation: FakeEvaluationSnapshot
    analysis: EvaluationAnalysis
    measurements: ResourceMeasurements
    gate_decision: GateDecision
