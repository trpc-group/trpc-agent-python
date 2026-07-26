"""Strict models for the evaluation and optimization loop example."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

SCORE_EPSILON = 1e-6


class StrictModel(BaseModel):
    """Base model that rejects misspelled or unsupported fields."""

    model_config = ConfigDict(extra="forbid")


class SplitName(str, Enum):
    """Supported dataset splits."""

    TRAIN = "train"
    VALIDATION = "validation"


class ChangeKind(str, Enum):
    """Per-case candidate change relative to baseline."""

    NEW_PASS = "new_pass"
    NEW_FAIL = "new_fail"
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"


class FailureCategory(str, Enum):
    """Stable failure-attribution categories required by the issue."""

    EXECUTION = "execution_error"
    TOOL_CALL = "tool_call_error"
    TOOL_ARGUMENT = "tool_argument_error"
    RUBRIC = "llm_rubric_failure"
    KNOWLEDGE = "knowledge_recall_failure"
    FORMAT = "format_failure"
    RESPONSE = "final_response_mismatch"
    OTHER = "other"


class GateConfig(StrictModel):
    """Acceptance policy loaded from gate.json."""

    primary_metric: str
    min_score_delta: float = 0.0
    critical_case_ids: list[str] = Field(default_factory=list)
    max_critical_regression: float = Field(default=0.0, ge=0.0)
    hard_case_ids: list[str] = Field(default_factory=list)
    hard_metric_names: list[str] = Field(default_factory=list)
    max_total_cost: float | None = Field(default=None, ge=0.0)
    max_duration_seconds: float | None = Field(default=None, gt=0.0)
    train_epsilon: float = Field(default=SCORE_EPSILON, ge=0.0)
    validation_epsilon: float = Field(default=SCORE_EPSILON, ge=0.0)

    @model_validator(mode="after")
    def _unique_ids(self) -> "GateConfig":
        for field_name in ("critical_case_ids", "hard_case_ids", "hard_metric_names"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} contains duplicates")
        return self


class InvocationSnapshot(StrictModel):
    """Minimal invocation material needed for attribution and audit."""

    final_text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class CaseSnapshot(StrictModel):
    """Aggregated result for one case across all configured runs."""

    case_id: str
    split: SplitName
    passed: bool
    hard_failure: bool
    metric_scores: dict[str, float | None] = Field(default_factory=dict)
    metric_statuses: dict[str, str] = Field(default_factory=dict)
    reasons: dict[str, str] = Field(default_factory=dict)
    error_message: str = ""
    actual: list[InvocationSnapshot] = Field(default_factory=list)
    expected: list[InvocationSnapshot] = Field(default_factory=list)


class EvaluationSnapshot(StrictModel):
    """Evaluation summary for a single prompt and dataset split."""

    split: SplitName
    primary_metric: str
    primary_score: float | None
    pass_rate: float
    metric_scores: dict[str, float | None] = Field(default_factory=dict)
    cases: list[CaseSnapshot] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0.0)


class Attribution(StrictModel):
    """Explainable primary attribution for one failed case."""

    case_id: str
    category: FailureCategory
    rule_id: str
    evidence: str


class CaseDelta(StrictModel):
    """Candidate change for a single case."""

    case_id: str
    baseline_passed: bool
    candidate_passed: bool
    baseline_score: float | None
    candidate_score: float | None
    score_delta: float | None
    change: ChangeKind
    hard_failure_added: bool = False


class SplitDelta(StrictModel):
    """Candidate changes for a dataset split."""

    split: SplitName
    baseline_score: float | None
    candidate_score: float | None
    score_delta: float | None
    cases: list[CaseDelta] = Field(default_factory=list)


class CostSummary(StrictModel):
    """Known pipeline costs and their observability."""

    optimizer_cost: float = Field(default=0.0, ge=0.0)
    external_cost: float = Field(default=0.0, ge=0.0)
    total_cost: float = Field(default=0.0, ge=0.0)
    cost_complete: bool = False


class GateCheck(StrictModel):
    """One auditable gate predicate."""

    name: str
    passed: bool
    reason: str


class GateDecision(StrictModel):
    """Final acceptance decision."""

    accepted: bool
    overfitting: bool
    checks: list[GateCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class AuditInfo(StrictModel):
    """Reproduction and provenance fields."""

    seed: int
    input_hashes: dict[str, str]
    model_name: str
    num_runs: int
    case_parallelism: int
    python_version: str
    sdk_version: str
    git_sha: str
    started_at: str
    finished_at: str
    stage_durations: dict[str, float]


class OptimizationSummary(StrictModel):
    """Subset of OptimizeResult consumed by this example."""

    status: str
    finish_reason: str
    best_prompts: dict[str, str] = Field(default_factory=dict)
    rounds: list[dict[str, Any]] = Field(default_factory=list)
    total_cost: float = 0.0
    error_message: str = ""


class OptimizationReport(StrictModel):
    """Top-level JSON and Markdown report schema."""

    schema_version: str = "v1"
    status: str
    baseline: dict[SplitName, EvaluationSnapshot]
    candidate: dict[SplitName, EvaluationSnapshot] = Field(default_factory=dict)
    delta: dict[SplitName, SplitDelta] = Field(default_factory=dict)
    attributions: list[Attribution] = Field(default_factory=list)
    attribution_counts: dict[FailureCategory, int] = Field(default_factory=dict)
    optimization: OptimizationSummary
    gate: GateDecision
    cost: CostSummary
    audit: AuditInfo
    source_updated: bool = False
    failures: list[str] = Field(default_factory=list)


class InputBundle(StrictModel):
    """Validated input paths and content hashes."""

    prompt_path: Path
    train_path: Path
    validation_path: Path
    optimizer_path: Path
    gate_path: Path
    hashes: dict[str, str]


class InputPaths(StrictModel):
    """Unvalidated CLI input paths."""

    prompt_path: Path
    train_path: Path
    validation_path: Path
    optimizer_path: Path
    gate_path: Path


class PipelineOptions(StrictModel):
    """CLI-independent options for one pipeline run."""

    paths: InputPaths
    output_dir: Path
    mode: str = "fake-model"
    fake_judge: bool = False
    trace_file: Path | None = None
    write_back: bool = False
    model_name: str = "fake-model"
    case_parallelism: int = 1


class PipelineResult(StrictModel):
    """Paths and report returned by the orchestration entry point."""

    report: OptimizationReport
    json_path: Path
    markdown_path: Path
