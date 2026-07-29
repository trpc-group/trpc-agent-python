# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Plain Python report types for the eval-optimize-loop example."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

FAILURE_CATEGORIES = [
    "final_answer_mismatch",
    "tool_call_error",
    "parameter_error",
    "llm_rubric_failed",
    "retrieval_failure",
    "format_error",
    "unknown",
]


@dataclass
class FailureAnalysis:
    """Rule-based attribution for one failed case."""

    category: str
    confidence: float
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


@dataclass
class BaselineCaseRecord:
    """One case-level baseline result serialized into optimization_report.json."""

    id: str
    metric_score: float
    metric_scores: dict[str, float]
    passed: bool
    failure_reason: str
    trace: dict[str, Any]
    latency: float
    cost: float = 0.0
    failure_analysis: FailureAnalysis | None = None
    evaluator_metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "metric_score": self.metric_score,
            "metric_scores": self.metric_scores,
            "passed": self.passed,
            "failure_reason": self.failure_reason,
            "trace": self.trace,
            "latency": self.latency,
            "cost": self.cost,
            "failure_analysis": self.failure_analysis.to_dict() if self.failure_analysis else None,
        }


@dataclass
class BaselineSplitResult:
    """Baseline results for one evalset split."""

    eval_set_id: str
    cases: list[BaselineCaseRecord] = field(default_factory=list)

    def failure_attribution_summary(self) -> dict[str, int]:
        summary = {category: 0 for category in FAILURE_CATEGORIES}
        for case in self.cases:
            if case.failure_analysis:
                summary[case.failure_analysis.category] = summary.get(case.failure_analysis.category, 0) + 1
        return summary

    def to_dict(self) -> dict[str, Any]:
        passed_count = sum(1 for case in self.cases if case.passed)
        failed_count = len(self.cases) - passed_count
        return {
            "eval_set_id": self.eval_set_id,
            "case_count": len(self.cases),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "failure_attribution_summary": self.failure_attribution_summary(),
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass
class CandidateEvaluation:
    """Candidate prompt evaluation snapshot serialized into optimization_report.json."""

    train: BaselineSplitResult
    val: BaselineSplitResult
    prompts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "train": self.train.to_dict(),
            "val": self.val.to_dict(),
            "prompts": self.prompts,
        }


@dataclass
class OptimizationRoundRecord:
    """One prompt optimization round for the report."""

    round: int
    optimized_field_names: list[str]
    before: dict[str, str]
    after: dict[str, str]
    reason: str
    accepted: bool
    validation_pass_rate: float = 0.0
    duration_seconds: float = 0.0
    cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "optimized_field_names": self.optimized_field_names,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
            "accepted": self.accepted,
            "validation_pass_rate": self.validation_pass_rate,
            "duration_seconds": self.duration_seconds,
            "cost": self.cost,
        }


@dataclass
class OptimizationRunRecord:
    """Prompt optimization loop summary."""

    target_prompt_names: list[str]
    baseline_prompts: dict[str, str]
    best_prompts: dict[str, str]
    rounds: list[OptimizationRoundRecord]
    total_rounds: int
    total_cost: float
    duration_seconds: float
    seed: int | None
    reason: str
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_prompt_names": self.target_prompt_names,
            "baseline_prompts": self.baseline_prompts,
            "best_prompts": self.best_prompts,
            "rounds": [record.to_dict() for record in self.rounds],
            "total_rounds": self.total_rounds,
            "total_cost": self.total_cost,
            "duration_seconds": self.duration_seconds,
            "seed": self.seed,
            "reason": self.reason,
            "artifacts": self.artifacts,
        }


@dataclass
class MetricDelta:
    """Metric-level comparison for one case."""

    metric_name: str
    baseline_score: float | None
    candidate_score: float | None
    score_delta: float | None
    baseline_passed: bool | None
    candidate_passed: bool | None
    status_transition: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "baseline_score": self.baseline_score,
            "candidate_score": self.candidate_score,
            "score_delta": self.score_delta,
            "baseline_passed": self.baseline_passed,
            "candidate_passed": self.candidate_passed,
            "status_transition": self.status_transition,
        }


@dataclass
class CaseDelta:
    """Case-level baseline vs candidate comparison."""

    id: str
    split: str
    change_type: str
    baseline: dict[str, Any]
    candidate: dict[str, Any]
    score_delta: float | None
    metric_deltas: list[MetricDelta]
    latency_delta: float | None
    cost_delta: float | None
    regression: bool
    improvement: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "split": self.split,
            "change_type": self.change_type,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "score_delta": self.score_delta,
            "metric_deltas": [metric.to_dict() for metric in self.metric_deltas],
            "latency_delta": self.latency_delta,
            "cost_delta": self.cost_delta,
            "regression": self.regression,
            "improvement": self.improvement,
            "notes": self.notes,
        }


@dataclass
class SplitDeltaSummary:
    """Aggregate and case-level delta for one eval split."""

    split: str
    baseline_score: float
    candidate_score: float
    score_delta: float
    baseline_pass_rate: float
    candidate_pass_rate: float
    pass_rate_delta: float
    case_deltas: list[CaseDelta]
    missing_candidate_case_ids: list[str] = field(default_factory=list)
    extra_candidate_case_ids: list[str] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_score": self.baseline_score,
            "candidate_score": self.candidate_score,
            "score_delta": self.score_delta,
            "baseline_pass_rate": self.baseline_pass_rate,
            "candidate_pass_rate": self.candidate_pass_rate,
            "pass_rate_delta": self.pass_rate_delta,
            "case_count": len(self.case_deltas),
            "new_pass_count": sum(1 for case in self.case_deltas if case.change_type == "new_pass"),
            "new_fail_count": sum(1 for case in self.case_deltas if case.change_type == "new_fail"),
            "regression_count": sum(1 for case in self.case_deltas if case.regression),
            "improvement_count": sum(1 for case in self.case_deltas if case.improvement),
            "missing_candidate_case_ids": self.missing_candidate_case_ids,
            "extra_candidate_case_ids": self.extra_candidate_case_ids,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_score": self.baseline_score,
            "candidate_score": self.candidate_score,
            "score_delta": self.score_delta,
            "baseline_pass_rate": self.baseline_pass_rate,
            "candidate_pass_rate": self.candidate_pass_rate,
            "pass_rate_delta": self.pass_rate_delta,
            "case_deltas": [case.to_dict() for case in self.case_deltas],
            "missing_candidate_case_ids": self.missing_candidate_case_ids,
            "extra_candidate_case_ids": self.extra_candidate_case_ids,
        }


@dataclass
class OptimizationDelta:
    """Top-level train/validation delta section."""

    train: SplitDeltaSummary
    val: SplitDeltaSummary

    def to_dict(self) -> dict[str, Any]:
        all_cases = self.train.case_deltas + self.val.case_deltas
        missing = self.train.missing_candidate_case_ids + self.val.missing_candidate_case_ids
        extra = self.train.extra_candidate_case_ids + self.val.extra_candidate_case_ids
        regression_count = sum(1 for case in all_cases if case.regression)
        improvement_count = sum(1 for case in all_cases if case.improvement)
        return {
            "summary": {
                "overall_change_type": _overall_change_type(regression_count, improvement_count),
                "train": self.train.to_summary_dict(),
                "val": self.val.to_summary_dict(),
                "regression_count": regression_count,
                "improvement_count": improvement_count,
                "new_pass_count": sum(1 for case in all_cases if case.change_type == "new_pass"),
                "new_fail_count": sum(1 for case in all_cases if case.change_type == "new_fail"),
                "missing_candidate_case_ids": missing,
                "extra_candidate_case_ids": extra,
            },
            "train": self.train.to_dict(),
            "val": self.val.to_dict(),
        }


@dataclass
class GateConfig:
    """Example-private gate policy loaded from gate.json."""

    min_val_score_gain: float = 0.05
    min_val_pass_rate_gain: float = 0.0
    allow_new_failures: bool = False
    allow_regressions: bool = False
    critical_case_ids: list[str] = field(default_factory=list)
    max_cost_delta: float = 0.0
    max_cost_ratio: float = 1.2
    require_validation_improvement: bool = True
    overfit_policy: dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "train_gain_min": 0.05,
        "val_drop_tolerance": 0.0,
    })

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "GateConfig":
        payload = payload or {}
        defaults = cls()
        overfit_policy = dict(defaults.overfit_policy)
        if isinstance(payload.get("overfit_policy"), dict):
            overfit_policy.update(payload["overfit_policy"])
        critical_case_ids = payload.get("critical_case_ids", [])
        if not isinstance(critical_case_ids, list):
            critical_case_ids = []
        return cls(
            min_val_score_gain=float(payload.get("min_val_score_gain", defaults.min_val_score_gain)),
            min_val_pass_rate_gain=float(payload.get("min_val_pass_rate_gain", defaults.min_val_pass_rate_gain)),
            allow_new_failures=bool(payload.get("allow_new_failures", defaults.allow_new_failures)),
            allow_regressions=bool(payload.get("allow_regressions", defaults.allow_regressions)),
            critical_case_ids=[str(case_id) for case_id in critical_case_ids],
            max_cost_delta=float(payload.get("max_cost_delta", defaults.max_cost_delta)),
            max_cost_ratio=float(payload.get("max_cost_ratio", defaults.max_cost_ratio)),
            require_validation_improvement=bool(
                payload.get("require_validation_improvement", defaults.require_validation_improvement)),
            overfit_policy=overfit_policy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_val_score_gain": self.min_val_score_gain,
            "min_val_pass_rate_gain": self.min_val_pass_rate_gain,
            "allow_new_failures": self.allow_new_failures,
            "allow_regressions": self.allow_regressions,
            "critical_case_ids": self.critical_case_ids,
            "max_cost_delta": self.max_cost_delta,
            "max_cost_ratio": self.max_cost_ratio,
            "require_validation_improvement": self.require_validation_improvement,
            "overfit_policy": self.overfit_policy,
        }


@dataclass
class GateRuleResult:
    """One auditable gate rule outcome."""

    rule_name: str
    passed: bool
    severity: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "passed": self.passed,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class GateDecision:
    """Final candidate accept/reject decision."""

    decision: str
    accepted: bool
    reason: str
    rule_results: list[GateRuleResult]
    summary: dict[str, Any]
    config: GateConfig
    recommended_action: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "accepted": self.accepted,
            "reason": self.reason,
            "rule_results": [result.to_dict() for result in self.rule_results],
            "summary": self.summary,
            "config": self.config.to_dict(),
            "recommended_action": self.recommended_action,
            "generated_at": self.generated_at,
        }


def _overall_change_type(regression_count: int, improvement_count: int) -> str:
    if regression_count and improvement_count:
        return "mixed"
    if regression_count:
        return "regressed"
    if improvement_count:
        return "improved"
    return "unchanged"


@dataclass
class BaselineOptimizationReport:
    """Full evaluation and optimization report."""

    mode: str
    train: BaselineSplitResult
    val: BaselineSplitResult
    metadata: dict[str, Any]
    run: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    failure_attribution: dict[str, Any] = field(default_factory=dict)
    candidate: CandidateEvaluation | None = None
    optimization: OptimizationRunRecord | None = None
    delta: OptimizationDelta | None = None
    gate_decision: GateDecision | None = None
    schema_version: str = "eval-optimize-loop-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run": self.run or {
                "mode": self.mode
            },
            "inputs": self.inputs,
            "config": self.config,
            "baseline": {
                "train": self.train.to_dict(),
                "val": self.val.to_dict(),
            },
            "failure_attribution": self.failure_attribution,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "delta": self.delta.to_dict() if self.delta else None,
            "gate_decision": self.gate_decision.to_dict() if self.gate_decision else None,
            "optimization": self.optimization.to_dict() if self.optimization else None,
            "metadata": self.metadata,
        }


@dataclass
class ReportPaths:
    """Locations of the generated report artifacts."""

    json_path: Any
    markdown_path: Any
