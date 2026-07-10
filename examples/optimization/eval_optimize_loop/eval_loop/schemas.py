"""Dataclass schemas used by the example optimization loop."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import is_dataclass
from typing import Any
from typing import Literal


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
        if "split" in payload and str(payload["split"]) != str(split):
            raise ValueError(
                f"eval case {case_id!r} split mismatch: payload has {payload['split']!r}, expected {split!r}"
            )
        expectation = payload.get("expectation")
        if not isinstance(expectation, dict):
            raise ValueError(f"eval case {case_id!r} is missing expectation object")
        return cls(
            case_id=str(case_id),
            split=str(split),
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
    metrics: dict[str, float] = field(default_factory=dict, kw_only=True)
    trace: dict[str, Any] = field(default_factory=dict)
    trace_available: bool = field(default=False, kw_only=True)
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
    prompt_fields: dict[str, str] = field(default_factory=dict)

    def bundle(self) -> dict[str, str]:
        """Return this candidate's complete prompt bundle."""

        if self.prompt_fields:
            return dict(self.prompt_fields)
        return {"system_prompt": self.prompt}


@dataclass(frozen=True)
class CostSummary:
    """Cost attribution for an optimization run."""

    optimizer: float = 0.0
    evaluator: float = 0.0
    agent: float = 0.0
    total: float = 0.0
    complete: bool = True


@dataclass(frozen=True)
class OptimizationRound:
    """One auditable optimizer round."""

    round_id: int
    candidate_id: str
    prompts: dict[str, str]
    rationale: str
    metrics: dict[str, float]
    cost: CostSummary
    duration_seconds: float


WritebackStatus = Literal[
    "rejected",
    "not_requested",
    "applied",
    "rolled_back",
    "rollback_failed",
]


@dataclass(frozen=True)
class WritebackResult:
    """Outcome of an optional source prompt writeback."""

    status: WritebackStatus
    before_hashes: dict[str, str] = field(default_factory=dict)
    after_hashes: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class OptimizationResult:
    """Backend-neutral optimization output."""

    candidates: list[CandidatePrompt]
    rounds: list[OptimizationRound]
    cost: CostSummary
    raw_summary: dict[str, Any] = field(default_factory=dict)


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
    gate_status: str = "applied"
    gate_not_applied_reason: str | None = None
    not_applied_checks: list[str] = field(default_factory=list)


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
    rounds: list[OptimizationRound] = field(default_factory=list)
    cost_summary: CostSummary = field(default_factory=CostSummary)
    writeback: WritebackResult = field(
        default_factory=lambda: WritebackResult(status="not_requested")
    )


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
