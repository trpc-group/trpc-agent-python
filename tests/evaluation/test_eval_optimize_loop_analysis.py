"""Unit tests for attribution, deltas, and acceptance gates."""

from examples.optimization.eval_optimize_loop.loop.analysis import GateInput
from examples.optimization.eval_optimize_loop.loop.analysis import attribute_case
from examples.optimization.eval_optimize_loop.loop.analysis import compare_snapshots
from examples.optimization.eval_optimize_loop.loop.analysis import evaluate_gate
from examples.optimization.eval_optimize_loop.loop.models import Attribution
from examples.optimization.eval_optimize_loop.loop.models import CaseSnapshot
from examples.optimization.eval_optimize_loop.loop.models import CostSummary
from examples.optimization.eval_optimize_loop.loop.models import EvaluationSnapshot
from examples.optimization.eval_optimize_loop.loop.models import FailureCategory
from examples.optimization.eval_optimize_loop.loop.models import GateConfig
from examples.optimization.eval_optimize_loop.loop.models import InvocationSnapshot
from examples.optimization.eval_optimize_loop.loop.models import SplitName


def _case(case_id: str, passed: bool, score: float | None) -> CaseSnapshot:
    return CaseSnapshot(
        case_id=case_id,
        split=SplitName.VALIDATION,
        passed=passed,
        hard_failure=score is None,
        metric_scores={"final_response_avg_score": score},
        metric_statuses={"final_response_avg_score": "PASSED" if passed else "FAILED"},
        actual=[InvocationSnapshot(final_text="actual")],
        expected=[InvocationSnapshot(final_text="expected")],
    )


def _snapshot(cases: list[CaseSnapshot], score: float | None) -> EvaluationSnapshot:
    return EvaluationSnapshot(
        split=SplitName.VALIDATION,
        primary_metric="final_response_avg_score",
        primary_score=score,
        pass_rate=sum(case.passed for case in cases) / len(cases) if cases else 0.0,
        metric_scores={"final_response_avg_score": score},
        cases=cases,
        duration_seconds=0.1,
    )


def _gate(**overrides) -> GateConfig:
    values = {
        "primary_metric": "final_response_avg_score",
        "min_score_delta": 0.1,
    }
    values.update(overrides)
    return GateConfig(**values)


def _gate_input(
    config: GateConfig,
    baseline: EvaluationSnapshot,
    candidate: EvaluationSnapshot,
    cost: CostSummary | None = None,
) -> GateInput:
    return GateInput(
        config=config,
        train_delta=compare_snapshots(baseline, candidate),
        validation_delta=compare_snapshots(baseline, candidate),
        baseline_validation=baseline,
        candidate_validation=candidate,
        cost=cost or CostSummary(optimizer_cost=0.0, total_cost=0.0, cost_complete=True),
        duration_seconds=1.0,
    )


def test_compare_snapshots_reports_new_pass_and_new_fail():
    baseline = _snapshot([_case("a", False, 0.0), _case("b", True, 1.0)], 0.5)
    candidate = _snapshot([_case("a", True, 1.0), _case("b", False, 0.0)], 0.5)

    delta = compare_snapshots(baseline, candidate)

    assert {item.change.value for item in delta.cases} == {"new_pass", "new_fail"}


def test_missing_candidate_case_is_hard_failure():
    baseline = _snapshot([_case("a", True, 1.0)], 1.0)
    candidate = _snapshot([], None)

    delta = compare_snapshots(baseline, candidate)

    assert delta.cases[0].hard_failure_added is True


def test_attribution_prefers_tool_argument_category():
    case = _case("tool", False, 0.0)
    case = case.model_copy(
        update={
            "actual": [InvocationSnapshot(tool_calls=[{
                "name": "lookup",
                "args": {
                    "id": 1
                }
            }])],
            "expected": [InvocationSnapshot(tool_calls=[{
                "name": "lookup",
                "args": {
                    "id": 2
                }
            }])],
        })

    result = attribute_case(case, "final_response_avg_score")

    assert isinstance(result, Attribution)
    assert result.category == FailureCategory.TOOL_ARGUMENT


def test_gate_rejects_score_regression_and_overfitting():
    baseline = _snapshot([_case("a", False, 0.0)], 0.0)
    candidate = _snapshot([_case("a", False, 0.0)], 0.0)
    config = _gate(min_score_delta=0.1)

    decision = evaluate_gate(_gate_input(config, baseline, candidate))

    assert decision.accepted is False
    assert "validation delta" in decision.reasons[0]


def test_gate_rejects_regression_even_with_negative_minimum_delta():
    baseline = _snapshot([_case("a", True, 1.0)], 1.0)
    candidate = _snapshot([_case("a", False, 0.0)], 0.0)

    decision = evaluate_gate(_gate_input(_gate(min_score_delta=-1.0), baseline, candidate))

    assert any(check.name == "validation_regression" and not check.passed for check in decision.checks)


def test_gate_rejects_configured_hard_case_and_incomplete_cost():
    baseline = _snapshot([_case("a", True, 1.0)], 1.0)
    candidate = _snapshot([_case("a", False, 0.0)], 0.0)
    config = _gate(min_score_delta=-1.0, hard_case_ids=["a"], max_total_cost=1.0)

    decision = evaluate_gate(
        _gate_input(
            config,
            baseline,
            candidate,
            CostSummary(optimizer_cost=0.1, total_cost=0.1, cost_complete=False),
        ))

    assert decision.accepted is False
    assert any(check.name == "hard_failures" and not check.passed for check in decision.checks)
    assert any(check.name == "cost" and not check.passed for check in decision.checks)


def test_gate_rejects_missing_critical_case():
    baseline = _snapshot([_case("a", True, 1.0)], 1.0)
    candidate = _snapshot([], None)
    config = _gate(min_score_delta=-1.0, critical_case_ids=["a"])

    decision = evaluate_gate(_gate_input(config, baseline, candidate))

    assert any(check.name == "critical_cases" and not check.passed for check in decision.checks)
