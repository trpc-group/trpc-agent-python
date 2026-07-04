from __future__ import annotations

from examples.optimization.eval_optimize_loop.eval_loop.gate import AcceptanceGate
from examples.optimization.eval_optimize_loop.eval_loop.report import compute_case_deltas
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CaseResult
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
    decision = AcceptanceGate({"protected_case_ids": []}).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
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
    decision = AcceptanceGate({"protected_case_ids": ["protected"]}).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
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
    decision = AcceptanceGate({"protected_case_ids": ["protected"]}).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
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

    decision = AcceptanceGate({"allow_new_hard_fail": False}).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
    )

    assert not decision.accepted
    assert decision.new_hard_failures == ["val_a"]
    assert decision.validation_new_failures == ["val_a"]


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

    decision = AcceptanceGate({"max_score_drop_per_case": 0.5, "allow_new_hard_fail": True}).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
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

    decision = AcceptanceGate({"max_total_cost": 0.001}).decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        deltas=deltas,
        cumulative_cost=0.001,
    )

    assert not decision.accepted
    assert decision.total_run_cost > 0.001


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
        )
        for case_id, score in scores
    ]
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=round(sum(case.score for case in cases) / len(cases), 6),
        passed=all(case.passed for case in cases),
        cost=0.001 * len(cases),
        cases=cases,
    )
