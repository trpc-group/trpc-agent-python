"""闭环 pipeline 的数据结构（pydantic v2，extra=forbid 保证契约硬）。

这些是对 SDK EvalCaseResult 的归一化投影 + 闭环自己的数据结构，不重复 SDK 的 EvalCase/EvalSet。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# 禁止多余字段：数据契约硬，report schema 漂移立即暴露
_STRICT = ConfigDict(extra="forbid")

Bucket = Literal["new_pass", "new_fail", "improved", "regressed", "unchanged"]
Decision = Literal["accept", "reject", "needs_review"]
RiskLevel = Literal["low", "medium", "high"]
RunMode = Literal["fake", "trace", "online"]
AttributionSource = Literal["rule", "counterfactual", "fallback"]


class CaseSnapshot(BaseModel):
    """单条 case 的归一化评测快照（从 SDK EvalCaseResult 投影）。"""

    model_config = _STRICT
    eval_id: str
    passed: bool
    score: float
    hard_fail: bool = False
    metrics: dict[str, float] = Field(default_factory=dict)
    primary_failure: Optional[str] = None
    failure_reasons: list[str] = Field(default_factory=list)
    actual_response: Optional[str] = None
    expected_response: Optional[str] = None
    key_trajectory: list[str] = Field(default_factory=list)


class SplitResult(BaseModel):
    """一个 split（train/validation）的聚合结果。"""

    model_config = _STRICT
    split: Literal["train", "validation"]
    pass_rate: float
    average_score: float
    cases: list[CaseSnapshot]


class BaselineResult(BaseModel):
    model_config = _STRICT
    train: SplitResult
    validation: SplitResult


class FailureAttribution(BaseModel):
    """单条失败 case 的归因结论。"""

    model_config = _STRICT
    category: str
    confidence: float
    evidence: str
    source: AttributionSource


class FailureAttributionSummary(BaseModel):
    model_config = _STRICT
    total_failed_cases: int
    explained_failed_cases: int
    coverage_rate: float
    category_counts: dict[str, int] = Field(default_factory=dict)
    by_case: dict[str, FailureAttribution] = Field(default_factory=dict)


class CaseDelta(BaseModel):
    """单条 case 的 baseline vs candidate 对比。"""

    model_config = _STRICT
    eval_id: str
    baseline_passed: bool
    candidate_passed: bool
    baseline_score: float
    candidate_score: float
    bucket: Bucket


class DeltaBuckets(BaseModel):
    model_config = _STRICT
    new_pass: list[str] = Field(default_factory=list)
    new_fail: list[str] = Field(default_factory=list)
    improved: list[str] = Field(default_factory=list)
    regressed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)


class SplitDelta(BaseModel):
    model_config = _STRICT
    split: Literal["train", "validation"]
    pass_rate_delta: float
    average_score_delta: float


class CandidateDelta(BaseModel):
    model_config = _STRICT
    train: SplitDelta
    validation: SplitDelta
    buckets: DeltaBuckets


class GateCheck(BaseModel):
    model_config = _STRICT
    check: str
    passed: bool
    required: bool = True
    actual: Optional[Any] = None
    expected: Optional[Any] = None
    reason: str = ""


class GateDecisionResult(BaseModel):
    model_config = _STRICT
    decision: Decision
    accepted: bool
    overfitting_detected: bool
    risk_level: RiskLevel
    checks: list[GateCheck]


class CandidateResult(BaseModel):
    model_config = _STRICT
    candidate_id: str
    source: Literal["captured", "fixture"]
    prompts: dict[str, str]
    train: SplitResult
    validation: SplitResult
    delta: CandidateDelta
    gate: GateDecisionResult
    audit_prompt_sha256: str
    optimizer_round: Optional[int] = None


class OptimizerInfo(BaseModel):
    model_config = _STRICT
    algorithm: str
    status: str
    rounds: int
    used_agent_optimizer: bool


class DataQuality(BaseModel):
    model_config = _STRICT
    passed: bool
    train_cases: int
    validation_cases: int
    cross_split_duplicates: int
    prompt_leakage_matches: int


class CostInfo(BaseModel):
    model_config = _STRICT
    measurement: Literal["unavailable", "measured_zero_offline", "measured_from_replay"]
    optimization_usd: float
    evaluation_usd: float
    total_usd: float


class AuditInfo(BaseModel):
    model_config = _STRICT
    run_id: str
    started_at: str
    finished_at: str
    duration_seconds: float
    seed: int
    config_sha256: str
    train_sha256: str
    validation_sha256: str
    baseline_prompt_sha256: dict[str, str]
    cost: CostInfo
    command: str


class OptimizationReport(BaseModel):
    """最终落盘的主报告。"""

    model_config = _STRICT
    schema_version: str
    status: Decision | Literal["failed"]
    mode: RunMode
    seed: int
    baseline: BaselineResult
    candidates: list[CandidateResult]
    selected_candidate_id: Optional[str] = None
    failure_attribution: FailureAttributionSummary
    optimizer: OptimizerInfo
    data_quality: DataQuality
    audit: AuditInfo
