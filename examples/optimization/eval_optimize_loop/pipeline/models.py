"""Strict, immutable stage contracts for the evaluation/optimization loop."""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import Field, field_validator, model_validator

from .schema import (
    StrictModel,
    finite_number as _finite,
    non_negative_number as _non_negative,
)


class Split(str, Enum):
    TRAIN = "train"
    VALIDATION = "validation"


class Phase(str, Enum):
    BASELINE = "baseline"
    CANDIDATE = "candidate"


class Transition(str, Enum):
    NEW_PASS = "NEW_PASS"
    NEW_FAIL = "NEW_FAIL"
    IMPROVED = "IMPROVED"
    REGRESSED = "REGRESSED"
    UNCHANGED = "UNCHANGED"


class Decision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    ERROR = "ERROR"


class FailureCategory(str, Enum):
    EVALUATION_ERROR = "EVALUATION_ERROR"
    TOOL_CALL_ERROR = "TOOL_CALL_ERROR"
    TOOL_ARGUMENT_ERROR = "TOOL_ARGUMENT_ERROR"
    KNOWLEDGE_RECALL_INSUFFICIENT = "KNOWLEDGE_RECALL_INSUFFICIENT"
    FORMAT_VIOLATION = "FORMAT_VIOLATION"
    LLM_RUBRIC_NOT_MET = "LLM_RUBRIC_NOT_MET"
    FINAL_RESPONSE_MISMATCH = "FINAL_RESPONSE_MISMATCH"
    UNKNOWN = "UNKNOWN"


Confidence = Literal["low", "medium", "high"]
RunMode = Literal["fake", "trace", "live"]


class RubricOutcome(StrictModel):
    id: str
    score: float
    passed: bool
    reason: Optional[str] = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("rubric outcome id must be non-empty and trimmed")
        return value

    @field_validator("score")
    @classmethod
    def finite_score(cls, value: float) -> float:
        return _finite(value, "rubric score")


class MetricRun(StrictModel):
    metric_name: str
    score: float
    threshold: float
    passed: bool
    reason: Optional[str] = None
    rubrics: tuple[RubricOutcome, ...] = ()

    @field_validator("score", "threshold")
    @classmethod
    def finite_score(cls, value: float) -> float:
        return _finite(value, "metric value")

    @model_validator(mode="after")
    def consistent_status(self) -> "MetricRun":
        if self.passed != (self.score >= self.threshold):
            raise ValueError("metric passed status does not match score >= threshold")
        for rubric in self.rubrics:
            if rubric.passed != (rubric.score >= self.threshold):
                raise ValueError(f"rubric {rubric.id!r} passed status does not match metric threshold")
        return self


class CaseRun(StrictModel):
    run_id: int = Field(ge=1)
    passed: bool
    metrics: tuple[MetricRun, ...]
    error: Optional[str] = None
    trace: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def consistent_status(self) -> "CaseRun":
        if not self.metrics:
            raise ValueError("a case run must contain metrics")
        expected = self.error is None and all(metric.passed for metric in self.metrics)
        if self.passed != expected:
            raise ValueError("case run passed status does not match metric statuses")
        return self


class MetricSnapshot(StrictModel):
    metric_name: str
    score: float
    threshold: float
    passed: bool

    @field_validator("score", "threshold")
    @classmethod
    def finite_score(cls, value: float) -> float:
        return _finite(value, "metric aggregate")


class CaseSnapshot(StrictModel):
    case_id: str
    passed: bool
    score: float
    metrics: tuple[MetricSnapshot, ...]
    runs: tuple[CaseRun, ...]
    error: Optional[str] = None

    @field_validator("score")
    @classmethod
    def finite_score(cls, value: float) -> float:
        return _finite(value, "case score")

    @model_validator(mode="after")
    def consistent_aggregate(self) -> "CaseSnapshot":
        if not self.metrics or not self.runs:
            raise ValueError("case snapshot requires metrics and runs")
        metric_names = tuple(metric.metric_name for metric in self.metrics)
        if len(set(metric_names)) != len(metric_names):
            raise ValueError("case snapshot metric names must be unique")
        if tuple(run.run_id for run in self.runs) != tuple(range(1, len(self.runs) + 1)):
            raise ValueError("case snapshot run IDs must be contiguous and 1-based")
        for run in self.runs:
            if tuple(metric.metric_name for metric in run.metrics) != metric_names:
                raise ValueError("case snapshot run metric schemas must match the aggregate")
        if self.passed != all(run.passed for run in self.runs):
            raise ValueError("case snapshot passed status does not match run statuses")
        expected_error = next((run.error for run in self.runs if run.error), None)
        if self.error != expected_error:
            raise ValueError("case snapshot error does not match the first run error")
        scores = [metric.score for metric in self.metrics]
        if self.score < min(scores) - 1e-12 or self.score > max(scores) + 1e-12:
            raise ValueError("case score must be a positive-weight aggregate of metric scores")
        return self


class EvaluationSnapshot(StrictModel):
    split: Split
    phase: Phase
    dataset_score: float
    pass_rate: float
    metric_scores: dict[str, float]
    case_ids: tuple[str, ...]
    cases: tuple[CaseSnapshot, ...]

    @field_validator("dataset_score", "pass_rate")
    @classmethod
    def finite_aggregate(cls, value: float) -> float:
        return _finite(value, "evaluation aggregate")

    @field_validator("metric_scores")
    @classmethod
    def finite_metric_scores(cls, value: dict[str, float]) -> dict[str, float]:
        for name, score in value.items():
            _finite(score, f"metric_scores[{name!r}]")
        return value

    @model_validator(mode="after")
    def complete_cases(self) -> "EvaluationSnapshot":
        if not self.cases:
            raise ValueError("evaluation snapshot requires at least one case")
        if self.case_ids != tuple(case.case_id for case in self.cases):
            raise ValueError("case_ids must match cases in order")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("evaluation snapshot case IDs must be unique")
        metric_names = set(self.metric_scores)
        if not metric_names or any(not name or name != name.strip() for name in metric_names):
            raise ValueError("evaluation snapshot metric names must be non-empty and trimmed")
        for case in self.cases:
            if {metric.metric_name for metric in case.metrics} != metric_names:
                raise ValueError("evaluation snapshot case metric schemas must match")
        expected_pass_rate = sum(case.passed for case in self.cases) / len(self.cases)
        if not math.isclose(self.pass_rate, expected_pass_rate, rel_tol=0, abs_tol=1e-12):
            raise ValueError("pass_rate does not match case statuses")
        case_scores = [case.score for case in self.cases]
        if self.dataset_score < min(case_scores) - 1e-12 or self.dataset_score > max(case_scores) + 1e-12:
            raise ValueError("dataset score must be a positive-weight aggregate of case scores")
        for name, aggregate in self.metric_scores.items():
            scores = [
                next(metric.score for metric in case.metrics if metric.metric_name == name) for case in self.cases
            ]
            if aggregate < min(scores) - 1e-12 or aggregate > max(scores) + 1e-12:
                raise ValueError(f"metric score {name!r} must be a positive-weight aggregate of case metrics")
        return self


class FailureAttribution(StrictModel):
    case_id: str
    primary: FailureCategory
    secondary: tuple[FailureCategory, ...] = ()
    reasons: tuple[str, ...]
    trigger_metrics: tuple[str, ...] = ()
    trigger_rubrics: tuple[str, ...] = ()
    evidence: tuple[str, ...]
    confidence: Confidence

    @model_validator(mode="after")
    def explanatory(self) -> "FailureAttribution":
        if not self.reasons or not self.evidence:
            raise ValueError("failure attribution needs reasons and evidence")
        if self.primary in self.secondary:
            raise ValueError("primary category cannot also be secondary")
        return self


class AttributionStatistics(StrictModel):
    total_failures: int = Field(default=0, ge=0)
    primary_category_counts: dict[FailureCategory, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_totals(self) -> "AttributionStatistics":
        if any(count < 1 for count in self.primary_category_counts.values()):
            raise ValueError("attribution category counts must be positive")
        if self.total_failures != sum(self.primary_category_counts.values()):
            raise ValueError("attribution total must equal primary category counts")
        return self


class AttributionSnapshot(StrictModel):
    split: Split
    phase: Phase
    failures: tuple[FailureAttribution, ...]
    statistics: AttributionStatistics = Field(default_factory=AttributionStatistics)

    @model_validator(mode="after")
    def statistics_match_failures(self) -> "AttributionSnapshot":
        expected = {
            category: sum(failure.primary == category for failure in self.failures)
            for category in FailureCategory
        }
        expected = {category: count for category, count in expected.items() if count}
        if self.statistics.total_failures != len(self.failures):
            raise ValueError("attribution statistics total does not match failures")
        if self.statistics.primary_category_counts != expected:
            raise ValueError("attribution category statistics do not match failures")
        return self


class InnerSplit(StrictModel):
    train_case_ids: tuple[str, ...]
    selection_case_ids: tuple[str, ...]
    train_hash: str
    selection_hash: str
    train_path: str
    selection_path: str


class CostSource(StrictModel):
    name: str
    cost_usd: Optional[float]
    upper_bound: bool = False
    model_calls: Optional[int] = Field(default=None, ge=0)
    metric_calls: Optional[int] = Field(default=None, ge=0)
    token_usage: dict[str, int] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("cost source name must be non-empty and trimmed")
        return value

    @field_validator("cost_usd")
    @classmethod
    def valid_cost(cls, value: Optional[float]) -> Optional[float]:
        return None if value is None else _non_negative(value, "cost")

    @model_validator(mode="after")
    def valid_upper_bound(self) -> "CostSource":
        if self.upper_bound and self.cost_usd is None:
            raise ValueError("unknown cost cannot be marked as an upper bound")
        return self

    @field_validator("token_usage")
    @classmethod
    def valid_tokens(cls, value: dict[str, int]) -> dict[str, int]:
        if any(amount < 0 for amount in value.values()):
            raise ValueError("token usage must be non-negative")
        return value


def _unreported_candidate_cost() -> tuple[CostSource, ...]:
    return (CostSource(name="unreported", cost_usd=None), )


class CandidateRound(StrictModel):
    round: int = Field(ge=1)
    candidate_prompts: dict[str, str]
    accepted: bool
    score: Optional[float] = None
    kind: Literal["deterministic", "reflective", "merge"] = "reflective"
    optimized_fields: tuple[str, ...] = ()
    metric_scores: dict[str, float] = Field(default_factory=dict)
    acceptance_reason: Optional[str] = None
    skip_reason: Optional[str] = None
    error_message: Optional[str] = None
    cost_usd: Optional[float] = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    duration_seconds: float = 0.0

    @field_validator("score")
    @classmethod
    def valid_score(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        value = _finite(value, "round score")
        if not 0 <= value <= 1:
            raise ValueError("round score must be in [0, 1]")
        return value

    @field_validator("optimized_fields")
    @classmethod
    def valid_optimized_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not name or name != name.strip() for name in value):
            raise ValueError("optimized fields must be non-empty and unique")
        return value

    @field_validator("metric_scores")
    @classmethod
    def valid_metric_scores(cls, value: dict[str, float]) -> dict[str, float]:
        for name, score in value.items():
            if not name or name != name.strip():
                raise ValueError("round metric names must be non-empty and trimmed")
            _finite(score, f"round metric score {name!r}")
        return value

    @field_validator("cost_usd")
    @classmethod
    def valid_cost(cls, value: Optional[float]) -> Optional[float]:
        return None if value is None else _non_negative(value, "round cost")

    @field_validator("token_usage")
    @classmethod
    def valid_tokens(cls, value: dict[str, int]) -> dict[str, int]:
        if any(amount < 0 for amount in value.values()):
            raise ValueError("round token usage must be non-negative")
        return value

    @field_validator("duration_seconds")
    @classmethod
    def valid_duration(cls, value: float) -> float:
        return _non_negative(value, "round duration")

    @model_validator(mode="after")
    def consistent_outcome(self) -> "CandidateRound":
        if self.skip_reason and self.error_message:
            raise ValueError("round cannot be both skipped and errored")
        if self.accepted and (not self.candidate_prompts or self.skip_reason or self.error_message):
            raise ValueError("accepted round must contain a normal candidate")
        if self.candidate_prompts and not (self.skip_reason or self.error_message) and self.score is None:
            raise ValueError("candidate round requires a validation score")
        if not self.candidate_prompts and not (self.skip_reason or self.error_message):
            raise ValueError("empty candidate round requires a skip or error reason")
        if not self.candidate_prompts and self.optimized_fields:
            raise ValueError("empty candidate round cannot report optimized fields")
        return self


class CandidateProposal(StrictModel):
    status: Literal["SUCCEEDED", "FAILED", "CANCELED"] = "SUCCEEDED"
    error_message: Optional[str] = None
    adapter_error: Optional[str] = None
    algorithm: str
    baseline_prompts: dict[str, str]
    prompts: dict[str, str]
    changed: bool
    stop_reason: Optional[str] = None
    rounds: tuple[CandidateRound, ...] = ()
    cost_sources: tuple[CostSource, ...] = Field(default_factory=_unreported_candidate_cost)
    duration_seconds: float = 0.0

    @field_validator("duration_seconds")
    @classmethod
    def valid_duration(cls, value: float) -> float:
        return _non_negative(value, "candidate duration")

    @model_validator(mode="after")
    def valid_round_history(self) -> "CandidateProposal":
        if self.status == "SUCCEEDED" and self.error_message:
            raise ValueError("successful candidate generation cannot contain an error")
        if self.adapter_error and self.status != "SUCCEEDED":
            raise ValueError("adapter errors only describe rejected successful backend results")
        if self.status == "FAILED" and not self.error_message:
            raise ValueError("failed candidate generation requires an error message")
        if set(self.baseline_prompts) != set(self.prompts):
            raise ValueError("candidate prompt keys must equal baseline keys")
        if self.changed != (self.prompts != self.baseline_prompts):
            raise ValueError("candidate changed flag is inconsistent")
        if tuple(round_.round for round_ in self.rounds) != tuple(range(1, len(self.rounds) + 1)):
            raise ValueError("candidate rounds must be contiguous and 1-based")
        source_names = tuple(source.name for source in self.cost_sources)
        if not source_names or len(set(source_names)) != len(source_names):
            raise ValueError("candidate cost source names must be non-empty and unique")
        registered = set(self.prompts)
        for round_ in self.rounds:
            if round_.candidate_prompts and set(round_.candidate_prompts) != registered:
                raise ValueError("candidate round prompt keys must equal registered prompt keys")
            if not set(round_.optimized_fields) <= registered:
                raise ValueError("round optimized fields must reference registered prompt keys")
        final_was_accepted = any(round_.accepted and round_.candidate_prompts == self.prompts for round_ in self.rounds)
        if self.changed and not final_was_accepted:
            raise ValueError("changed final candidate must equal an accepted round")
        return self


class MetricDelta(StrictModel):
    metric_name: str
    baseline: float
    candidate: float
    delta: float

    @field_validator("baseline", "candidate", "delta")
    @classmethod
    def finite_value(cls, value: float) -> float:
        return _finite(value, "metric delta")

    @model_validator(mode="after")
    def consistent_delta(self) -> "MetricDelta":
        if not math.isclose(
                self.delta,
                self.candidate - self.baseline,
                rel_tol=0,
                abs_tol=1e-12,
        ):
            raise ValueError("metric delta does not match candidate minus baseline")
        return self


class CaseComparison(StrictModel):
    case_id: str
    baseline_passed: bool
    candidate_passed: bool
    baseline_score: float
    candidate_score: float
    delta: float
    metrics: tuple[MetricDelta, ...]
    transition: Transition
    critical: bool = False
    hard: bool = False
    baseline_attribution: Optional[FailureAttribution] = None
    candidate_attribution: Optional[FailureAttribution] = None
    new_hard_failure: bool = False

    @field_validator("baseline_score", "candidate_score", "delta")
    @classmethod
    def finite_score(cls, value: float) -> float:
        return _finite(value, "case comparison score")

    @model_validator(mode="after")
    def consistent_transition(self) -> "CaseComparison":
        if not math.isclose(
                self.delta,
                self.candidate_score - self.baseline_score,
                rel_tol=0,
                abs_tol=1e-12,
        ):
            raise ValueError("case delta does not match candidate minus baseline")
        metric_names = tuple(metric.metric_name for metric in self.metrics)
        if len(set(metric_names)) != len(metric_names):
            raise ValueError("case comparison metric names must be unique")
        expected_new_pass = not self.baseline_passed and self.candidate_passed
        expected_new_fail = self.baseline_passed and not self.candidate_passed
        if expected_new_pass != (self.transition == Transition.NEW_PASS):
            raise ValueError("NEW_PASS transition does not match case statuses")
        if expected_new_fail != (self.transition == Transition.NEW_FAIL):
            raise ValueError("NEW_FAIL transition does not match case statuses")
        expected_hard_failure = self.hard and expected_new_fail
        if self.new_hard_failure != expected_hard_failure:
            raise ValueError("new hard failure flag does not match case statuses")
        for attribution in (self.baseline_attribution, self.candidate_attribution):
            if attribution is not None and attribution.case_id != self.case_id:
                raise ValueError("case comparison attribution ID does not match the case")
        return self


class ComparisonSnapshot(StrictModel):
    split: Split
    score_delta: float
    pass_rate_delta: float
    metric_deltas: dict[str, float]
    cases: tuple[CaseComparison, ...]

    @field_validator("score_delta", "pass_rate_delta")
    @classmethod
    def finite_value(cls, value: float) -> float:
        return _finite(value, "comparison delta")

    @field_validator("metric_deltas")
    @classmethod
    def finite_metric_deltas(cls, value: dict[str, float]) -> dict[str, float]:
        for name, delta in value.items():
            if not name or name != name.strip():
                raise ValueError("comparison metric names must be non-empty and trimmed")
            _finite(delta, f"comparison metric delta {name!r}")
        return value

    @model_validator(mode="after")
    def consistent_aggregate(self) -> "ComparisonSnapshot":
        if not self.cases:
            raise ValueError("comparison snapshot requires at least one case")
        case_ids = tuple(case.case_id for case in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("comparison case IDs must be unique")
        metric_names = set(self.metric_deltas)
        if not metric_names:
            raise ValueError("comparison snapshot requires metric deltas")
        for case in self.cases:
            if {metric.metric_name for metric in case.metrics} != metric_names:
                raise ValueError("comparison case metric schemas must match aggregate metrics")
        expected_pass_delta = (sum(case.candidate_passed
                                   for case in self.cases) - sum(case.baseline_passed
                                                                 for case in self.cases)) / len(self.cases)
        if not math.isclose(
                self.pass_rate_delta,
                expected_pass_delta,
                rel_tol=0,
                abs_tol=1e-12,
        ):
            raise ValueError("pass-rate delta does not match case statuses")
        case_deltas = [case.delta for case in self.cases]
        if self.score_delta < min(case_deltas) - 1e-12 or self.score_delta > max(case_deltas) + 1e-12:
            raise ValueError("score delta must be a positive-weight aggregate of case deltas")
        for name, aggregate in self.metric_deltas.items():
            deltas = [
                next(metric.delta for metric in case.metrics if metric.metric_name == name) for case in self.cases
            ]
            if aggregate < min(deltas) - 1e-12 or aggregate > max(deltas) + 1e-12:
                raise ValueError(f"metric delta {name!r} must be a positive-weight aggregate of case metrics")
        return self


class CostSummary(StrictModel):
    sources: tuple[CostSource, ...]
    total_cost_usd: Optional[float]

    @field_validator("total_cost_usd")
    @classmethod
    def valid_total(cls, value: Optional[float]) -> Optional[float]:
        return None if value is None else _non_negative(value, "total cost")

    @model_validator(mode="after")
    def consistent_total(self) -> "CostSummary":
        names = tuple(source.name for source in self.sources)
        if len(set(names)) != len(names):
            raise ValueError("cost source names must be unique")
        has_unknown = any(source.cost_usd is None for source in self.sources)
        if has_unknown and self.total_cost_usd is not None:
            raise ValueError("total cost must be unknown when any source cost is unknown")
        if not has_unknown and self.total_cost_usd is None:
            raise ValueError("total cost must be reported when all source costs are known")
        if not has_unknown and not math.isclose(
                self.total_cost_usd or 0,
                sum(source.cost_usd or 0 for source in self.sources),
                rel_tol=0,
                abs_tol=1e-12,
        ):
            raise ValueError("total cost does not equal the sum of cost sources")
        return self


class GateCheck(StrictModel):
    code: str
    passed: bool
    observed: Any
    threshold: Any
    message: str


class GateDecision(StrictModel):
    decision: Decision
    accepted: bool
    checks: tuple[GateCheck, ...]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def consistent_decision(self) -> "GateDecision":
        if self.accepted != (self.decision == Decision.ACCEPT):
            raise ValueError("accepted flag must match ACCEPT decision")
        return self


class SnapshotPair(StrictModel):
    train: Optional[EvaluationSnapshot] = None
    validation: Optional[EvaluationSnapshot] = None

    @model_validator(mode="after")
    def has_completed_snapshot(self) -> "SnapshotPair":
        if self.train is None and self.validation is None:
            raise ValueError("snapshot pair requires at least one completed split")
        if self.train is not None and self.train.split != Split.TRAIN:
            raise ValueError("train snapshot must use the train split")
        if self.validation is not None and self.validation.split != Split.VALIDATION:
            raise ValueError("validation snapshot must use the validation split")
        return self


class ComparisonPair(StrictModel):
    train: Optional[ComparisonSnapshot] = None
    validation: Optional[ComparisonSnapshot] = None

    @model_validator(mode="after")
    def has_completed_comparison(self) -> "ComparisonPair":
        if self.train is None and self.validation is None:
            raise ValueError("comparison pair requires at least one completed split")
        if self.train is not None and self.train.split != Split.TRAIN:
            raise ValueError("train comparison must use the train split")
        if self.validation is not None and self.validation.split != Split.VALIDATION:
            raise ValueError("validation comparison must use the validation split")
        return self


class AttributionPair(StrictModel):
    train: Optional[AttributionSnapshot] = None
    validation: Optional[AttributionSnapshot] = None

    @model_validator(mode="after")
    def has_completed_attribution(self) -> "AttributionPair":
        if self.train is None and self.validation is None:
            raise ValueError("attribution pair requires at least one completed split")
        if self.train is not None and self.train.split != Split.TRAIN:
            raise ValueError("train attribution must use the train split")
        if self.validation is not None and self.validation.split != Split.VALIDATION:
            raise ValueError("validation attribution must use the validation split")
        return self


class Reproducibility(StrictModel):
    reproducible: bool
    command: Optional[str]
    reason: Optional[str]
    git_commit: Optional[str]
    git_dirty: Optional[bool]


class SourceApplication(StrictModel):
    requested: bool
    applied: bool
    baseline_hashes: dict[str, str]
    final_hashes: dict[str, str]


class ArtifactRecord(StrictModel):
    path: str
    sha256: str
    byte_size: int = Field(ge=0)


class RunError(StrictModel):
    stage: str
    error_type: str
    message: str


class OptimizationReport(StrictModel):
    schema_version: Literal["v2"] = "v2"
    run_id: str
    status: Decision
    mode: RunMode
    stage: str
    started_at: str
    finished_at: str
    duration_seconds: float
    reproducibility: Reproducibility
    inputs: dict[str, Any]
    prompts: dict[str, Any]
    baseline: Optional[SnapshotPair] = None
    candidate: Optional[SnapshotPair] = None
    delta: Optional[ComparisonPair] = None
    failure_attribution: Optional[AttributionPair] = None
    candidate_failure_attribution: Optional[AttributionPair] = None
    optimization: Optional[dict[str, Any]] = None
    cost: Optional[CostSummary] = None
    gate_decision: Optional[GateDecision] = None
    source_application: SourceApplication
    artifacts: tuple[ArtifactRecord, ...] = ()
    errors: tuple[RunError, ...] = ()

    @field_validator("duration_seconds")
    @classmethod
    def valid_duration(cls, value: float) -> float:
        return _non_negative(value, "duration")

    @model_validator(mode="after")
    def terminal_consistency(self) -> "OptimizationReport":
        if self.status in (Decision.ACCEPT, Decision.REJECT):
            if self.gate_decision is None or self.gate_decision.decision != self.status:
                raise ValueError("terminal report status must match gate decision")
            for pair, name in (
                (self.baseline, "baseline"),
                (self.candidate, "candidate"),
                (self.delta, "delta"),
            ):
                if pair is None or pair.train is None or pair.validation is None:
                    raise ValueError(f"terminal report requires complete {name} results")
        if self.status == Decision.ERROR and not self.errors:
            raise ValueError("ERROR report must include at least one error")
        return self
