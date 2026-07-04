"""Dataclass schemas used by the example optimization loop."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import is_dataclass
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    """One deterministic evaluation case."""

    case_id: str
    split: str
    input: str
    expectation: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    protected: bool = False
    simulated_outputs: dict[str, str] = field(default_factory=dict)
    expected_failure_category: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any], split: str) -> "EvalCase":
        case_id = payload.get("case_id") or payload.get("id")
        if not case_id:
            raise ValueError(f"eval case is missing id/case_id: {payload!r}")
        expectation = payload.get("expectation")
        if not isinstance(expectation, dict):
            raise ValueError(f"eval case {case_id!r} is missing expectation object")
        return cls(
            case_id=str(case_id),
            split=str(payload.get("split") or split),
            input=str(payload.get("input") or payload.get("user_input") or ""),
            expectation=dict(expectation),
            tags=list(payload.get("tags") or []),
            protected=bool(payload.get("protected", False)),
            simulated_outputs=dict(payload.get("simulated_outputs") or expectation.get("simulated_outputs") or {}),
            expected_failure_category=payload.get("expected_failure_category")
            or expectation.get("expected_failure_category"),
        )


@dataclass(frozen=True)
class CaseResult:
    """Evaluation result for one case under one prompt."""

    case_id: str
    split: str
    score: float
    passed: bool
    output: str
    trace: dict[str, Any] = field(default_factory=dict)
    failure_category: str | None = None
    failure_reason: str | None = None
    evidence: str | None = None
    cost: float = 0.0
    hard_failed: bool = False
    expected_failure_category: str | None = None


@dataclass(frozen=True)
class EvalResult:
    """Aggregate result for one prompt on one split."""

    prompt_id: str
    split: str
    score: float
    passed: bool
    cost: float
    cases: list[CaseResult]

    def by_case_id(self) -> dict[str, CaseResult]:
        return {case.case_id: case for case in self.cases}


@dataclass(frozen=True)
class CandidatePrompt:
    """A proposed prompt candidate."""

    candidate_id: str
    prompt: str
    rationale: str
    prompt_diff: str


@dataclass(frozen=True)
class CaseDelta:
    """Per-case score delta from baseline to candidate."""

    candidate_id: str
    case_id: str
    split: str
    baseline_score: float
    candidate_score: float
    delta: float
    baseline_passed: bool
    candidate_passed: bool
    regression: bool
    delta_type: str


@dataclass(frozen=True)
class GateDecision:
    """Configurable acceptance gate result for one candidate."""

    candidate_id: str
    accepted: bool
    reasons: list[str]
    train_score_delta: float
    validation_score_delta: float
    new_hard_failures: list[str]
    protected_regressions: list[str]
    validation_new_failures: list[str]
    excessive_score_drops: list[str]
    overfit_detected: bool
    candidate_cost: float
    cumulative_cost: float
    total_run_cost: float
    cost: float


@dataclass(frozen=True)
class OptimizationReport:
    """Complete persisted audit report for the loop."""

    schema_version: str
    run: dict[str, Any]
    baseline: dict[str, EvalResult]
    baseline_train: EvalResult
    baseline_validation: EvalResult
    candidates: list[dict[str, Any]]
    delta: dict[str, Any]
    per_case_deltas: list[CaseDelta]
    failure_attribution_summary: dict[str, Any]
    gate_decisions: list[GateDecision]
    selected_candidate: str | None
    audit: dict[str, Any]


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses and nested containers into JSON-serializable data."""

    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    return value
