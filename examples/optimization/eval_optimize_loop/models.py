from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from trpc_agent_sdk.evaluation import EvalBaseModel, EvalConfig


class GateConfig(EvalBaseModel):
    min_improvement: float = Field(default=0.0, description="Min val pass_rate delta.")
    allow_new_fails: bool = Field(default=False, description="Allow new failures in val.")
    protected_case_ids: list[str] = Field(default_factory=list, description="Cases that must not degrade.")
    max_cost_usd: Optional[float] = Field(default=None, description="USD cost cap.")
    max_duration_seconds: int = Field(default=180, description="Pipeline timeout in seconds.")


class PipelineConfig(EvalBaseModel):
    mode: Literal["live", "trace"] = Field(description="Pipeline mode.")
    output_dir: str = Field(default="outputs", description="Artifact output directory.")
    evaluate: EvalConfig = Field(description="Evaluation config (metrics, num_runs).")
    gate: GateConfig = Field(default_factory=GateConfig, description="Acceptance gate rules.")
    seed: int = Field(default=42, description="Random seed for reproducibility.")

    # Trace mode fields
    baseline_prompt_path: Optional[str] = Field(default=None)
    candidate_prompt_path: Optional[str] = Field(default=None)
    train_baseline_evalset: Optional[str] = Field(default=None)
    train_candidate_evalset: Optional[str] = Field(default=None)
    val_baseline_evalset: Optional[str] = Field(default=None)
    val_candidate_evalset: Optional[str] = Field(default=None)

    # Live mode fields
    live_train_evalset: Optional[str] = Field(default=None)
    live_val_evalset: Optional[str] = Field(default=None)
    optimizer_config_path: Optional[str] = Field(default=None)
    target_prompt_name: Optional[str] = Field(default=None)


class PerCaseResult(EvalBaseModel):
    case_id: str
    passed: bool
    metric_scores: dict[str, float] = Field(default_factory=dict)


class SplitResult(EvalBaseModel):
    pass_rate: float = Field(default=0.0, description="Fraction of cases that passed.")
    metric_breakdown: dict[str, float] = Field(default_factory=dict, description="Mean score per metric.")
    per_case: dict[str, PerCaseResult] = Field(default_factory=dict, description="Per-case results keyed by case_id.")


class PerCaseDelta(EvalBaseModel):
    newly_passing: list[str] = Field(default_factory=list, description="Case IDs: failed baseline, passed candidate.")
    newly_failing: list[str] = Field(default_factory=list, description="Case IDs: passed baseline, failed candidate.")
    score_deltas: dict[str, dict[str, float]] = Field(default_factory=dict, description="case_id -> {metric: delta}.")
    unchanged: list[str] = Field(default_factory=list, description="Case IDs: same status in both.")


class SplitDelta(EvalBaseModel):
    train: PerCaseDelta = Field(default_factory=PerCaseDelta)
    val: PerCaseDelta = Field(default_factory=PerCaseDelta)
    train_pass_rate_delta: float = Field(default=0.0)
    val_pass_rate_delta: float = Field(default=0.0)


class FailureCategory(EvalBaseModel):
    count: int = Field(default=0, description="Number of cases in this category.")
    case_ids: list[str] = Field(default_factory=list, description="Case IDs in this category.")


class FailureAttribution(EvalBaseModel):
    total_cases: int = Field(default=0)
    failed_cases: int = Field(default=0)
    categories: dict[str, FailureCategory] = Field(default_factory=dict, description="Keyed by failure category name.")


class GateDecision(EvalBaseModel):
    decision: Literal["ACCEPT", "REJECT"]
    reasons: list[str] = Field(default_factory=list, description="One reason per rule evaluation.")
    overfitting_warning: bool = Field(default=False, description="True when train improves but val degrades.")


class PipelineResult(EvalBaseModel):
    schema_version: str = Field(default="v1")
    mode: str = ""
    gate_decision: str = ""
    gate_reasons: list[str] = Field(default_factory=list)
    baseline: dict[str, SplitResult] = Field(default_factory=dict)
    candidate: dict[str, SplitResult] = Field(default_factory=dict)
    delta: SplitDelta = Field(default_factory=SplitDelta)
    failure_attribution: FailureAttribution = Field(default_factory=FailureAttribution)
    overfitting_warning: bool = Field(default=False)
    duration_seconds: float = Field(default=0.0)
    cost_usd: float = Field(default=0.0)
    seed: int = Field(default=42)
    started_at: str = ""
    finished_at: str = ""
