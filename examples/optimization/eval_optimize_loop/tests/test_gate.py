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
