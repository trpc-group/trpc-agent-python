from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureType(str, Enum):
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    FINAL_RESPONSE_MISMATCH = "final_response_mismatch"
    TOOL_SELECTION_ERROR = "tool_selection_error"
    TOOL_ARGUMENT_ERROR = "tool_argument_error"
    FORMAT_VIOLATION = "format_violation"
    KNOWLEDGE_RECALL_INSUFFICIENT = "knowledge_recall_insufficient"
    LLM_RUBRIC_NOT_MET = "llm_rubric_not_met"
    SAFETY_VIOLATION = "safety_violation"
    UNKNOWN = "unknown"


class ToolCallSnapshot(StrictModel):
    name: str
    arguments: Any = Field(default_factory=dict)


class ToolResponseSnapshot(StrictModel):
    name: str
    response: Any = None


class FailureAttribution(StrictModel):
    eval_id: str
    primary_type: FailureType
    secondary_types: list[FailureType] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(min_length=1)
    source: Literal["rule", "judge", "fallback"]


class CaseSnapshot(StrictModel):
    eval_id: str
    split: Literal["train", "validation"]
    run_count: int
    passed: bool
    hard_failed: bool
    aggregate_score: float
    metric_scores: dict[str, float]
    metric_thresholds: dict[str, float]
    metric_passed: dict[str, bool]
    trace_digest: str
    metric_reasons: dict[str, list[str]] = Field(default_factory=dict)
    execution_errors: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    failure_types: list[FailureType] = Field(default_factory=list)
    failure_attribution: FailureAttribution | None = None
    final_response: str | None = None
    expected_response: str | None = None
    tool_calls: list[ToolCallSnapshot] = Field(default_factory=list)
    expected_tool_calls: list[ToolCallSnapshot] = Field(default_factory=list)
    tool_responses: list[ToolResponseSnapshot] = Field(default_factory=list)


class CaseDelta(StrictModel):
    eval_id: str
    baseline_passed: bool
    candidate_passed: bool
    transition: Literal["NEW_PASS", "REGRESSION", "IMPROVED", "DEGRADED", "UNCHANGED"]
    baseline_score: float
    candidate_score: float
    score_delta: float
    metric_deltas: dict[str, float]
    critical: bool
    hard_fail_added: bool
    new_failure_types: list[FailureType] = Field(default_factory=list)
    resolved_failure_types: list[FailureType] = Field(default_factory=list)


class SplitReport(StrictModel):
    cases: list[CaseSnapshot]
    pass_rate: float
    aggregate_score: float

    @classmethod
    def from_cases(cls, cases: list[CaseSnapshot]) -> "SplitReport":
        if not cases:
            return cls(cases=[], pass_rate=0.0, aggregate_score=0.0)
        return cls(
            cases=cases,
            pass_rate=sum(case.passed for case in cases) / len(cases),
            aggregate_score=sum(case.aggregate_score for case in cases) / len(cases),
        )


class GateSettings(StrictModel):
    min_validation_score_delta: float = 0.05
    min_validation_pass_rate_delta: float = 0.0
    max_new_hard_fails: int = 0
    max_validation_regressions: int = 0
    critical_case_ids: list[str] = Field(default_factory=list)
    allow_critical_case_regression: bool = False
    reject_when_train_improves_but_validation_declines: bool = True
    max_generalization_gap: float | None = None
    max_generation_cost_usd: float | None = None
    max_duration_seconds: float | None = None
    tie_policy: Literal["reject"] = "reject"


class GateRuleResult(StrictModel):
    rule: str
    passed: bool
    actual: Any
    expected: Any
    reason: str


class GateDecision(StrictModel):
    accepted: bool
    risk_level: Literal["low", "medium", "high"]
    rules: list[GateRuleResult]
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)


class ReproducibilitySettings(StrictModel):
    seed: int = 42


class DatasetSettings(StrictModel):
    train_path: Path = Path("train.evalset.json")
    validation_path: Path = Path("val.evalset.json")


class PipelineSettings(StrictModel):
    datasets: DatasetSettings = Field(default_factory=DatasetSettings)
    reproducibility: ReproducibilitySettings = Field(default_factory=ReproducibilitySettings)
    gate: GateSettings = Field(default_factory=GateSettings)
    scoring_epsilon: float = 0.000001
    metric_weights: dict[str, float] = Field(default_factory=lambda: {"final_response_avg_score": 0.6, "fake_rubric_score": 0.4})
    metric_floors: dict[str, float] = Field(default_factory=dict)
    candidate_validation: "CandidateValidationSettings" = Field(default_factory=lambda: CandidateValidationSettings())
    write_back_when_accepted: bool = False


class CandidateValidationSettings(StrictModel):
    scope: Literal["best_only", "accepted_rounds", "all"] = "accepted_rounds"


class CandidateRecord(StrictModel):
    candidate_id: str
    prompts: dict[str, str]
    source: Literal["fixture", "agent_optimizer"] = "fixture"
    generation_cost_usd: float | None = None
    duration_seconds: float | None = None
    round_index: int | None = None
    optimizer_accepted: bool | None = None
    optimizer_reason: str = ""


class CandidateReport(StrictModel):
    candidate_id: str
    accepted: bool
    reasons: list[str] = Field(default_factory=list)
    train: SplitReport | None = None
    validation: SplitReport | None = None
    gate: GateDecision | None = None
    validation_case_deltas: list[CaseDelta] = Field(default_factory=list)
    independently_evaluated: bool = False
    source: Literal["fixture", "agent_optimizer"] = "fixture"
    generation_cost_usd: float | None = None
    duration_seconds: float | None = None


class AuditReferences(StrictModel):
    config_snapshot_path: Path = Path("input.snapshot.json")
    input_snapshot_path: Path = Path("input.snapshot.json")
    environment_snapshot_path: Path = Path("environment.snapshot.json")
    raw_reports_path: Path = Path("audit/raw_reports.json")
    normalized_reports_path: Path = Path("audit/normalized_reports.json")
    candidate_reports_path: Path = Path("audit/candidate_reports.json")
    gate_decisions_path: Path = Path("audit/gate_decisions.json")


class OptimizationReport(StrictModel):
    schema_version: str = "1.0"
    mode: Literal["fake", "trace", "live"]
    seed: int
    selected_candidate_id: str | None = None
    candidates: list[CandidateReport] = Field(default_factory=list)
    baseline_train: SplitReport | None = None
    baseline_validation: SplitReport | None = None
    source_integrity: Literal["restored", "unknown"] = "restored"
    run_metadata: dict[str, Any] = Field(default_factory=dict)
    audit_references: AuditReferences = Field(default_factory=AuditReferences)
    attribution_summary: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def empty(cls, *, mode: Literal["fake", "trace", "live"], seed: int) -> "OptimizationReport":
        return cls(mode=mode, seed=seed)
