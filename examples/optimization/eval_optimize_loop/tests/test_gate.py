from __future__ import annotations

from ..gate import apply_gate
from ..models import (
    PerCaseDelta,
    PerCaseResult,
    SplitDelta,
    SplitResult,
    GateConfig,
    GateDecision,
)


def _make_delta(
    train_pass_rate_delta: float = 0.0,
    val_pass_rate_delta: float = 0.0,
    train_newly_passing: list[str] | None = None,
    train_newly_failing: list[str] | None = None,
    val_newly_passing: list[str] | None = None,
    val_newly_failing: list[str] | None = None,
) -> SplitDelta:
    return SplitDelta(
        train_pass_rate_delta=train_pass_rate_delta,
        val_pass_rate_delta=val_pass_rate_delta,
        train=PerCaseDelta(
            newly_passing=train_newly_passing or [],
            newly_failing=train_newly_failing or [],
            unchanged=[],
        ),
        val=PerCaseDelta(
            newly_passing=val_newly_passing or [],
            newly_failing=val_newly_failing or [],
            unchanged=[],
        ),
    )


def test_gate_accept_no_new_fails_improvement():
    """Val improved, no new fails → ACCEPT."""
    delta = _make_delta(val_pass_rate_delta=0.33)
    gate = GateConfig(min_improvement=0.0, allow_new_fails=False)
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.decision == "ACCEPT"
    assert not decision.overfitting_warning


def test_gate_reject_below_min_improvement():
    """Val improvement below threshold → REJECT."""
    delta = _make_delta(val_pass_rate_delta=0.05)
    gate = GateConfig(min_improvement=0.1, allow_new_fails=False)
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.decision == "REJECT"
    assert any("min_improvement" in r.lower() for r in decision.reasons)


def test_gate_reject_new_fails():
    """New failures in val, allow_new_fails=False → REJECT."""
    delta = _make_delta(val_pass_rate_delta=0.33, val_newly_failing=["case_1"])
    gate = GateConfig(min_improvement=0.0, allow_new_fails=False)
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.decision == "REJECT"
    assert any("newly failing" in r.lower() for r in decision.reasons)


def test_gate_accept_new_fails_allowed():
    """New failures in val, allow_new_fails=True → ACCEPT (if min_improvement met)."""
    delta = _make_delta(val_pass_rate_delta=0.5, val_newly_failing=["case_1"])
    gate = GateConfig(min_improvement=0.0, allow_new_fails=True)
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.decision == "ACCEPT"


def test_gate_reject_cost_over_budget():
    """Cost exceeds max_cost_usd → REJECT."""
    delta = _make_delta(val_pass_rate_delta=0.5)
    gate = GateConfig(min_improvement=0.0, max_cost_usd=1.0)
    decision = apply_gate(delta, gate, cost_usd=5.0, duration_seconds=10.0)
    assert decision.decision == "REJECT"
    assert any("cost" in r.lower() for r in decision.reasons)


def test_gate_reject_duration_over_budget():
    """Duration exceeds max_duration_seconds → REJECT."""
    delta = _make_delta(val_pass_rate_delta=0.5)
    gate = GateConfig(min_improvement=0.0, max_duration_seconds=60)
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=120.0)
    assert decision.decision == "REJECT"
    assert any("duration" in r.lower() for r in decision.reasons)


def test_gate_overfitting_warning():
    """Train improves but val does not → overfitting_warning=True."""
    delta = _make_delta(train_pass_rate_delta=0.5, val_pass_rate_delta=0.0)
    gate = GateConfig(min_improvement=0.0, allow_new_fails=False)
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.overfitting_warning is True
    assert any("overfitting" in r.lower() for r in decision.reasons)


def test_gate_reject_overfitting_with_degradation():
    """Train improves but val degrades → overfitting_warning + REJECT (below min_improvement)."""
    delta = _make_delta(train_pass_rate_delta=0.5, val_pass_rate_delta=-0.2)
    gate = GateConfig(min_improvement=0.0, allow_new_fails=False)
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.overfitting_warning is True
    assert decision.decision == "REJECT"


def test_gate_protected_case_degradation():
    """Protected case score drops → REJECT."""
    delta = _make_delta(val_pass_rate_delta=0.33)
    delta.val.score_deltas = {"case_key": {"m1": -0.2}}
    gate = GateConfig(min_improvement=0.0, protected_case_ids=["case_key"])
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.decision == "REJECT"
    assert any("protected" in r.lower() for r in decision.reasons)


def test_gate_protected_case_no_degradation():
    """Protected case score same or better → ok."""
    delta = _make_delta(val_pass_rate_delta=0.33)
    delta.val.score_deltas = {"case_key": {"m1": 0.1}}
    gate = GateConfig(min_improvement=0.0, protected_case_ids=["case_key"])
    decision = apply_gate(delta, gate, cost_usd=0.0, duration_seconds=10.0)
    assert decision.decision == "ACCEPT"


def test_gate_all_rules_pass():
    """All rules pass → ACCEPT with all reasons."""
    delta = _make_delta(val_pass_rate_delta=0.33)
    delta.train_pass_rate_delta = 0.0  # No overfitting
    gate = GateConfig(min_improvement=0.0, allow_new_fails=False, max_cost_usd=10.0, max_duration_seconds=300)
    decision = apply_gate(delta, gate, cost_usd=1.0, duration_seconds=30.0)
    assert decision.decision == "ACCEPT"
    assert len(decision.reasons) >= 4  # one per rule that passed
