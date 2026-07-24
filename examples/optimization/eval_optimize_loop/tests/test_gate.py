from __future__ import annotations

from examples.optimization.eval_optimize_loop.eval_loop.gate import AcceptanceGate
from examples.optimization.eval_optimize_loop.eval_loop.report import compute_case_deltas
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CaseResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CostSummary
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalResult


def test_gate_rejects_train_improvement_with_validation_regression():
    baseline_train = _eval("baseline", "train", [("train_a", 0.0), ("train_b", 1.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0), ("train_b", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_a", 1.0), ("val_b", 1.0)])
    candidate_val = _eval("candidate", "validation", [("val_a", 1.0), ("val_b", 0.0)])

    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )
    decision = AcceptanceGate({
        "protected_case_ids": []
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(),
    )

    assert not decision.accepted
    assert any("train score improved but validation score regressed" in reason for reason in decision.reasons)
    assert decision.overfit_detected


def test_gate_rejects_protected_case_regression():
    baseline_train = _eval("baseline", "train", [("train_a", 1.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("protected", 1.0), ("val_a", 0.0), ("val_b", 0.0)])
    candidate_val = _eval("candidate", "validation", [("protected", 0.0), ("val_a", 1.0), ("val_b", 1.0)])

    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )
    decision = AcceptanceGate({
        "protected_case_ids": ["protected"]
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(),
    )

    assert not decision.accepted
    assert decision.protected_regressions == ["protected"]
    assert any("protected cases regressed" in reason for reason in decision.reasons)


def test_gate_accepts_safe_candidate():
    baseline_train = _eval("baseline", "train", [("train_json", 0.0), ("train_exact", 0.0), ("train_ok", 1.0)])
    candidate_train = _eval("candidate", "train", [("train_json", 1.0), ("train_exact", 1.0), ("train_ok", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_json", 0.0), ("val_text", 1.0), ("protected", 1.0)])
    candidate_val = _eval("candidate", "validation", [("val_json", 1.0), ("val_text", 1.0), ("protected", 1.0)])

    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )
    decision = AcceptanceGate({
        "protected_case_ids": ["protected"]
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(),
    )

    assert decision.accepted
    assert decision.protected_regressions == []
    assert decision.new_hard_failures == []


def test_gate_rejects_new_hard_failure_when_not_allowed():
    baseline_train = _eval("baseline", "train", [("train_a", 1.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_a", 1.0), ("val_b", 0.0), ("val_c", 0.0)])
    candidate_val = _eval("candidate", "validation", [("val_a", 0.0), ("val_b", 1.0), ("val_c", 1.0)])
    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )

    decision = AcceptanceGate({
        "allow_new_hard_fail": False
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(),
    )

    assert not decision.accepted
    assert decision.new_hard_failures == ["val_a"]
    assert decision.validation_new_failures == ["val_a"]


def test_gate_rejects_soft_failure_that_becomes_hard_failure():
    baseline_train = _eval("baseline", "train", [("train_a", 1.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("soft_fail", 0.5), ("improved", 0.0), ("ok", 1.0)])
    candidate_val = _eval("candidate", "validation", [("soft_fail", 0.0), ("improved", 1.0), ("ok", 1.0)])
    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )

    decision = AcceptanceGate({
        "allow_new_hard_fail": False,
        "max_score_drop_per_case": 1.0,
        "min_val_score_improvement": 0.01,
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(),
    )

    assert not decision.accepted
    assert decision.new_hard_failures == ["soft_fail"]
    assert any("new hard failures" in reason for reason in decision.reasons)


def test_gate_rejects_excessive_score_drop():
    baseline_train = _eval("baseline", "train", [("train_a", 1.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_a", 1.0), ("val_b", 0.0), ("val_c", 0.0)])
    candidate_val = _eval("candidate", "validation", [("val_a", 0.4), ("val_b", 1.0), ("val_c", 1.0)])
    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )

    decision = AcceptanceGate({
        "max_score_drop_per_case": 0.5,
        "allow_new_hard_fail": True
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(),
    )

    assert not decision.accepted
    assert decision.excessive_score_drops == ["val_a"]


def test_gate_rejects_cost_budget():
    baseline_train = _eval("baseline", "train", [("train_a", 0.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_a", 0.0)])
    candidate_val = _eval("candidate", "validation", [("val_a", 1.0)])
    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )

    decision = AcceptanceGate({
        "max_total_cost": 0.001
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(optimizer=0.001, total=0.001, complete=True),
        cumulative_cost=0.001,
    )

    assert not decision.accepted
    assert decision.total_run_cost > 0.001
    assert decision.total_run_cost == decision.cumulative_cost + decision.candidate_cost


def test_gate_rejects_incomplete_cost_when_budget_is_configured():
    baseline_train = _eval("baseline", "train", [("train_a", 0.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_a", 0.0)])
    candidate_val = _eval("candidate", "validation", [("val_a", 1.0)])

    decision = AcceptanceGate({
        "max_total_cost": 100.0
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=compute_case_deltas(
            candidate_id="candidate",
            baseline_train=baseline_train,
            baseline_validation=baseline_val,
            candidate_train=candidate_train,
            candidate_validation=candidate_val,
        ),
        cost_summary=CostSummary(optimizer=0.5, total=0.5, complete=False),
        cumulative_cost=0.502,
    )

    assert decision.accepted is False
    assert any("cost_unavailable for configured max_total_cost" in reason for reason in decision.reasons)
    assert decision.total_run_cost == 0.504


def test_gate_skips_cost_completeness_check_when_budget_is_disabled():
    baseline_train = _eval("baseline", "train", [("train_a", 0.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_a", 0.0)])
    candidate_val = _eval("candidate", "validation", [("val_a", 1.0)])

    decision = AcceptanceGate({
        "max_total_cost": None
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=compute_case_deltas(
            candidate_id="candidate",
            baseline_train=baseline_train,
            baseline_validation=baseline_val,
            candidate_train=candidate_train,
            candidate_validation=candidate_val,
        ),
        cost_summary=CostSummary(optimizer=10.0, total=10.0, complete=False),
        cumulative_cost=10.002,
    )

    assert decision.accepted is True
    assert all("cost_unavailable" not in reason for reason in decision.reasons)
    assert decision.total_run_cost == 10.004


def test_gate_rejects_new_validation_failure_even_when_it_is_not_hard():
    baseline_train = _eval("baseline", "train", [("train_a", 1.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("new_soft", 1.0), ("up_a", 0.0), ("up_b", 0.0)])
    candidate_val = _eval("candidate", "validation", [("new_soft", 0.5), ("up_a", 1.0), ("up_b", 1.0)])
    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )

    decision = AcceptanceGate({
        "allow_new_hard_fail": True,
        "max_score_drop_per_case": 1.0,
        "max_total_cost": None,
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cost_summary=CostSummary(),
    )

    assert decision.accepted is False
    assert decision.validation_new_failures == ["new_soft"]
    assert any("new validation failures" in reason for reason in decision.reasons)


def test_gate_does_not_add_cost_summary_total_twice():
    baseline_train = _eval("baseline", "train", [("train_a", 0.0)])
    candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    baseline_val = _eval("baseline", "validation", [("val_a", 0.0)])
    candidate_val = _eval("candidate", "validation", [("val_a", 1.0)])

    decision = AcceptanceGate({
        "max_total_cost": 1.0
    }).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=compute_case_deltas(
            candidate_id="candidate",
            baseline_train=baseline_train,
            baseline_validation=baseline_val,
            candidate_train=candidate_train,
            candidate_validation=candidate_val,
        ),
        cost_summary=CostSummary(optimizer=0.4, total=0.4, complete=True),
        cumulative_cost=0.6,
    )

    assert decision.total_run_cost == 0.602
    assert decision.accepted is True


def test_gate_rejects_duplicate_missing_and_empty_case_results():
    valid_train = _eval("baseline", "train", [("train_a", 0.0)])
    valid_candidate_train = _eval("candidate", "train", [("train_a", 1.0)])
    valid_val = _eval("baseline", "validation", [("val_a", 0.0)])
    valid_candidate_val = _eval("candidate", "validation", [("val_a", 1.0)])
    scenarios = [
        (
            _eval("baseline", "train", [("train_a", 0.0), ("train_a", 0.0)]),
            valid_candidate_train,
            valid_val,
            valid_candidate_val,
            "duplicate case IDs",
        ),
        (
            valid_train,
            _eval("candidate", "train", [("other", 1.0)]),
            valid_val,
            valid_candidate_val,
            "case ID set mismatch",
        ),
        (
            _empty_eval("baseline", "train"),
            _empty_eval("candidate", "train"),
            valid_val,
            valid_candidate_val,
            "empty case results",
        ),
    ]

    for baseline_train, candidate_train, baseline_val, candidate_val, expected in scenarios:
        decision = AcceptanceGate({
            "max_total_cost": None
        }).decide(
            candidate_id="candidate",
            baseline_train=baseline_train,
            baseline_validation=baseline_val,
            candidate_train=candidate_train,
            candidate_validation=candidate_val,
            deltas=[],
            cost_summary=CostSummary(),
        )
        assert decision.accepted is False
        assert any(expected in reason for reason in decision.reasons)
        assert decision.gate_status == "applied"
        assert decision.not_applied_checks == []


def test_case_delta_types_are_classified():
    baseline_train = _eval("baseline", "train", [("new_pass", 0.0), ("new_fail", 1.0)])
    candidate_train = _eval("candidate", "train", [("new_pass", 1.0), ("new_fail", 0.0)])
    baseline_val = _eval("baseline", "validation", [("up", 0.2), ("down", 0.8), ("same", 0.5)])
    candidate_val = _eval("candidate", "validation", [("up", 0.5), ("down", 0.5), ("same", 0.5)])

    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
    )

    by_case = {delta.case_id: delta.delta_type for delta in deltas}
    assert by_case["new_pass"] == "new_pass"
    assert by_case["new_fail"] == "new_fail"
    assert by_case["up"] == "score_up"
    assert by_case["down"] == "score_down"
    assert by_case["same"] == "unchanged"


def _eval(prompt_id: str, split: str, scores: list[tuple[str, float]]) -> EvalResult:
    cases = [
        CaseResult(
            case_id=case_id,
            split=split,
            score=score,
            passed=score >= 1.0,
            output="",
            hard_failed=score <= 0.0,
        ) for case_id, score in scores
    ]
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=round(sum(case.score for case in cases) / len(cases), 6),
        passed=all(case.passed for case in cases),
        cost=0.001 * len(cases),
        cases=cases,
    )


def _empty_eval(prompt_id: str, split: str) -> EvalResult:
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=0.0,
        passed=False,
        cost=0.0,
        cases=[],
    )
