"""Deterministic attribution and gate policy tests."""

from __future__ import annotations

import pytest

from examples.optimization.eval_optimize_loop.pipeline.attribution import attribute_failures
from examples.optimization.eval_optimize_loop.pipeline.configuration import (
    AttributionConfig,
    GateConfig,
)
from examples.optimization.eval_optimize_loop.pipeline.gate import evaluate_gate
from examples.optimization.eval_optimize_loop.pipeline.models import (
    CandidateProposal,
    CandidateRound,
    CaseComparison,
    CaseRun,
    CaseSnapshot,
    ComparisonSnapshot,
    CostSource,
    CostSummary,
    Decision,
    EvaluationSnapshot,
    FailureCategory,
    MetricDelta,
    MetricRun,
    MetricSnapshot,
    Phase,
    RubricOutcome,
    Split,
    Transition,
)

_MISSING = object()


def _invocation(tool: str | None = None, args: dict | None = None) -> dict:
    payload = {
        "invocationId": "ignored",
        "userContent": {
            "role": "user",
            "parts": [{
                "text": "query"
            }]
        },
        "finalResponse": {
            "role": "model",
            "parts": [{
                "text": "answer"
            }]
        },
    }
    if tool:
        payload["intermediateData"] = {"toolUses": [{"name": tool, "args": args or {}}]}
    return payload


def _failed_snapshot(
        metric_name: str = "custom",
        *,
        reason: str | None = "unmapped failure",
        error: str | None = None,
        actual: dict | None = None,
        expected: dict | None | object = _MISSING,
        rubrics: tuple[RubricOutcome, ...] = (),
) -> EvaluationSnapshot:
    metric = MetricRun(
        metric_name=metric_name,
        score=0,
        threshold=1,
        passed=False,
        reason=reason,
        rubrics=rubrics,
    )
    run = CaseRun(
        run_id=1,
        passed=False,
        metrics=(metric, ),
        error=error,
        trace=({
            "actual": actual or _invocation(),
            "expected": _invocation() if expected is _MISSING else expected,
        }, ),
    )
    case = CaseSnapshot(
        case_id="case",
        passed=False,
        score=0,
        metrics=(MetricSnapshot(metric_name=metric_name, score=0, threshold=1, passed=False), ),
        runs=(run, ),
        error=error,
    )
    return EvaluationSnapshot(
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        dataset_score=0,
        pass_rate=0,
        metric_scores={metric_name: 0},
        case_ids=("case", ),
        cases=(case, ),
    )


_ATTRIBUTION_BENCHMARK = (
    (_failed_snapshot(error="backend failed"), AttributionConfig(), FailureCategory.EVALUATION_ERROR),
    (
        _failed_snapshot(actual=_invocation("search"), expected=_invocation("calculate")),
        AttributionConfig(),
        FailureCategory.TOOL_CALL_ERROR,
    ),
    (
        _failed_snapshot(
            actual=_invocation("search", {"q": "wrong"}),
            expected=_invocation("search", {"q": "right"}),
        ),
        AttributionConfig(),
        FailureCategory.TOOL_ARGUMENT_ERROR,
    ),
    (
        _failed_snapshot("llm_rubric_knowledge_recall"),
        AttributionConfig(),
        FailureCategory.KNOWLEDGE_RECALL_INSUFFICIENT,
    ),
    (
        _failed_snapshot("format_metric"),
        AttributionConfig(metric_categories={"format_metric": FailureCategory.FORMAT_VIOLATION}),
        FailureCategory.FORMAT_VIOLATION,
    ),
    (
        _failed_snapshot("llm_rubric_response"),
        AttributionConfig(),
        FailureCategory.LLM_RUBRIC_NOT_MET,
    ),
    (
        _failed_snapshot("final_response_avg_score"),
        AttributionConfig(),
        FailureCategory.FINAL_RESPONSE_MISMATCH,
    ),
    (_failed_snapshot(), AttributionConfig(), FailureCategory.UNKNOWN),
)


@pytest.mark.parametrize(
    ("snapshot", "config", "expected"),
    _ATTRIBUTION_BENCHMARK,
)
def test_attribution_taxonomy(snapshot, config, expected) -> None:
    result = attribute_failures(snapshot, config, max_text_chars=200)
    assert result.failures[0].primary == expected
    assert result.failures[0].reasons
    assert result.failures[0].evidence


def test_attribution_policy_matrix_matches_every_expected_category() -> None:
    assert all(
        attribute_failures(snapshot, config, max_text_chars=200).failures[0].primary == expected
        for snapshot, config, expected in _ATTRIBUTION_BENCHMARK)


def test_reference_free_failure_uses_metric_attribution_without_expected_trace() -> None:
    result = attribute_failures(
        _failed_snapshot("llm_rubric_response", expected=None),
        AttributionConfig(),
        max_text_chars=200,
    )
    assert result.failures[0].primary == FailureCategory.LLM_RUBRIC_NOT_MET


def test_tool_difference_evidence_is_recursively_sanitized() -> None:
    result = attribute_failures(
        _failed_snapshot(
            actual=_invocation("search", {"apiKey": "actual-tool-secret"}),
            expected=_invocation("search", {"apiKey": "expected-tool-secret"}),
        ),
        AttributionConfig(),
        max_text_chars=1000,
    )
    evidence = "\n".join(result.failures[0].evidence)
    assert "actual-tool-secret" not in evidence
    assert "expected-tool-secret" not in evidence
    assert "[REDACTED]" in evidence


def test_structured_argument_difference_outranks_generic_tool_metric() -> None:
    result = attribute_failures(
        _failed_snapshot(
            "tool_trajectory_avg_score",
            actual=_invocation("search", {"q": "wrong"}),
            expected=_invocation("search", {"q": "right"}),
        ),
        AttributionConfig(),
        max_text_chars=1000,
    )
    assert result.failures[0].primary == FailureCategory.TOOL_ARGUMENT_ERROR


def test_attribution_uses_only_failed_rubric_outcomes() -> None:
    snapshot = _failed_snapshot(
        "llm_rubric_response",
        rubrics=(
            RubricOutcome(id="passed_rule", score=1, passed=True),
            RubricOutcome(id="failed_rule", score=0, passed=False),
        ),
    )
    config = AttributionConfig(rubric_categories={
        "passed_rule": FailureCategory.TOOL_CALL_ERROR,
        "failed_rule": FailureCategory.FORMAT_VIOLATION,
    })

    result = attribute_failures(snapshot, config, max_text_chars=200)

    failure = result.failures[0]
    assert failure.primary == FailureCategory.FORMAT_VIOLATION
    assert failure.trigger_rubrics == ("failed_rule", )


def _comparison(
    split: Split,
    *,
    score_delta: float = 0.1,
    pass_delta: float = 1.0,
    critical: bool = False,
    hard: bool = False,
) -> ComparisonSnapshot:
    if pass_delta > 0:
        baseline_passed, candidate_passed = False, True
        transition = Transition.NEW_PASS
    elif pass_delta < 0:
        baseline_passed, candidate_passed = True, False
        transition = Transition.NEW_FAIL
    else:
        baseline_passed = candidate_passed = True
        transition = (Transition.IMPROVED
                      if score_delta > 0 else Transition.REGRESSED if score_delta < 0 else Transition.UNCHANGED)
    return ComparisonSnapshot(
        split=split,
        score_delta=score_delta,
        pass_rate_delta=pass_delta,
        metric_deltas={"quality": score_delta},
        cases=(CaseComparison(
            case_id="case",
            baseline_passed=baseline_passed,
            candidate_passed=candidate_passed,
            baseline_score=0,
            candidate_score=score_delta,
            delta=score_delta,
            metrics=(MetricDelta(
                metric_name="quality",
                baseline=0,
                candidate=score_delta,
                delta=score_delta,
            ), ),
            transition=transition,
            critical=critical,
            hard=hard,
            new_hard_failure=hard and baseline_passed and not candidate_passed,
        ), ),
    )


def _candidate() -> CandidateProposal:
    return CandidateProposal(
        algorithm="fake",
        baseline_prompts={"system": "old"},
        prompts={"system": "new"},
        changed=True,
        rounds=(CandidateRound(
            round=1,
            candidate_prompts={"system": "new"},
            accepted=True,
            score=1,
        ), ),
    )


def _gate(**overrides):
    values = {
        "train": _comparison(Split.TRAIN),
        "validation": _comparison(Split.VALIDATION),
        "candidate": _candidate(),
        "cost": CostSummary(
            sources=(CostSource(name="fake", cost_usd=0), ),
            total_cost_usd=0,
        ),
        "duration_seconds": 1,
        "baseline_prompt_hashes": {
            "system": "old"
        },
        "candidate_prompt_hashes": {
            "system": "new"
        },
        "config": GateConfig(metric_max_regression={"quality": 0}),
    }
    values.update(overrides)
    return evaluate_gate(**values)


def test_gate_accepts_only_when_checks_pass_in_fixed_order() -> None:
    result = _gate()
    assert result.decision == Decision.ACCEPT
    assert [check.code for check in result.checks] == [
        "REPORT_COMPLETE",
        "CANDIDATE_CHANGED",
        "VALIDATION_SCORE_DELTA",
        "VALIDATION_PASS_RATE_DELTA",
        "NO_NEW_HARD_FAIL",
        "CRITICAL_CASE_NON_REGRESSION",
        "METRIC_NON_REGRESSION",
        "COST_BUDGET",
        "DURATION_BUDGET",
        "OVERFIT_GUARD",
    ]


def test_gate_rejects_train_only_improvement_as_overfit() -> None:
    validation = _comparison(Split.VALIDATION, score_delta=-0.1, pass_delta=0)
    result = _gate(validation=validation)
    assert result.decision == Decision.REJECT
    assert "OVERFIT_TRAIN_UP_VALIDATION_DOWN" in result.reasons


def test_gate_fails_closed_when_enabled_cost_is_unknown() -> None:
    cost = CostSummary(
        sources=(CostSource(name="live-agent", cost_usd=None), ),
        total_cost_usd=None,
    )
    result = _gate(cost=cost, config=GateConfig(max_cost_usd=1, metric_max_regression={"quality": 0}))
    assert result.decision == Decision.REJECT
    assert "COST_UNAVAILABLE" in result.reasons


def test_gate_returns_error_for_missing_configured_metric() -> None:
    result = _gate(config=GateConfig(metric_max_regression={"missing": 0}))
    assert result.decision == Decision.ERROR
    assert result.reasons == ("GATE_INPUT_INVALID", )


def test_gate_policy_matrix_matches_every_expected_decision() -> None:
    unchanged = CandidateProposal(
        algorithm="fake",
        baseline_prompts={"system": "old"},
        prompts={"system": "old"},
        changed=False,
    )
    unknown_cost = CostSummary(
        sources=(CostSource(name="live", cost_usd=None), ),
        total_cost_usd=None,
    )
    scenarios = (
        (_gate(), Decision.ACCEPT),
        (_gate(report_complete=False), Decision.REJECT),
        (
            _gate(
                candidate=unchanged,
                candidate_prompt_hashes={"system": "old"},
            ),
            Decision.REJECT,
        ),
        (
            _gate(config=GateConfig(min_validation_score_delta=0.2)),
            Decision.REJECT,
        ),
        (
            _gate(config=GateConfig(min_validation_pass_rate_delta=1.1)),
            Decision.REJECT,
        ),
        (
            _gate(validation=_comparison(
                Split.VALIDATION,
                score_delta=-0.1,
                pass_delta=-1,
                hard=True,
            )),
            Decision.REJECT,
        ),
        (
            _gate(validation=_comparison(
                Split.VALIDATION,
                score_delta=-0.1,
                pass_delta=-1,
                critical=True,
            )),
            Decision.REJECT,
        ),
        (
            _gate(
                validation=_comparison(Split.VALIDATION, score_delta=-0.1, pass_delta=0),
                config=GateConfig(
                    min_validation_score_delta=-1,
                    min_validation_pass_rate_delta=-1,
                    metric_max_regression={"quality": 0},
                ),
            ),
            Decision.REJECT,
        ),
        (
            _gate(
                cost=unknown_cost,
                config=GateConfig(max_cost_usd=1, metric_max_regression={"quality": 0}),
            ),
            Decision.REJECT,
        ),
        (
            _gate(
                duration_seconds=2,
                config=GateConfig(max_duration_seconds=1, metric_max_regression={"quality": 0}),
            ),
            Decision.REJECT,
        ),
        (
            _gate(config=GateConfig(metric_max_regression={"missing": 0})),
            Decision.ERROR,
        ),
    )
    assert len(scenarios) >= 6
    assert all(actual.decision == expected for actual, expected in scenarios)
