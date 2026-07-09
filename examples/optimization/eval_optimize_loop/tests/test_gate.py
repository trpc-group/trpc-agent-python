"""Tests for gate decision module."""

import pytest

from pipeline.gate import (
    GateDecision,
    GateResult,
    evaluate_gate,
)


class TestGateDecision:
    """Tests for GateDecision enum."""

    def test_accept_value(self):
        assert GateDecision.ACCEPT.value == "accept"

    def test_reject_value(self):
        assert GateDecision.REJECT.value == "reject"

    def test_needs_review_value(self):
        assert GateDecision.NEEDS_REVIEW.value == "needs_review"


class TestEvaluateGate:
    """Tests for evaluate_gate() multi-dimensional decision."""

    # --- ACCEPT scenarios ---

    def test_accept_clear_improvement(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.85,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.ACCEPT

    def test_accept_large_improvement(self):
        result = evaluate_gate(
            baseline_pass_rate=0.3, candidate_pass_rate=0.9,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.ACCEPT

    # --- NEEDS_REVIEW scenarios ---

    def test_needs_review_insufficient_improvement(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.52,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.NEEDS_REVIEW

    def test_needs_review_perfect_already(self):
        result = evaluate_gate(
            baseline_pass_rate=1.0, candidate_pass_rate=1.0,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.05,
        )
        assert result.decision == GateDecision.NEEDS_REVIEW

    def test_needs_review_new_failures(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.7,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.05,
            baseline_failed=["case_001"],
            candidate_failed=["case_001", "case_002"],
        )
        assert result.decision in (GateDecision.NEEDS_REVIEW, GateDecision.REJECT)

    # --- REJECT scenarios ---

    def test_reject_degradation(self):
        result = evaluate_gate(
            baseline_pass_rate=0.8, candidate_pass_rate=0.6,
            baseline_metrics={}, candidate_metrics={},
        )
        assert result.decision == GateDecision.REJECT
        assert "degraded" in result.reason.lower()

    def test_reject_critical_case(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.6,
            baseline_metrics={}, candidate_metrics={},
            critical_case_ids=["critical_001"],
            baseline_failed=[],
            candidate_failed=["critical_001"],
        )
        assert result.decision == GateDecision.REJECT
        assert "Critical case" in result.reason

    def test_reject_over_budget(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.9,
            baseline_metrics={}, candidate_metrics={},
            max_cost=5.0, optimization_cost=15.0,
        )
        assert result.decision == GateDecision.REJECT
        assert "exceeds budget" in result.reason.lower()

    def test_reject_full_degradation(self):
        result = evaluate_gate(
            baseline_pass_rate=0.9, candidate_pass_rate=0.1,
            baseline_metrics={}, candidate_metrics={},
        )
        assert result.decision == GateDecision.REJECT

    # --- Edge cases ---

    def test_zero_to_hero(self):
        """Baseline 0% → candidate 80% — should accept."""
        result = evaluate_gate(
            baseline_pass_rate=0.0, candidate_pass_rate=0.8,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.ACCEPT

    def test_tiny_improvement_at_high_baseline(self):
        """95% → 97% with 2% threshold — needs review."""
        result = evaluate_gate(
            baseline_pass_rate=0.95, candidate_pass_rate=0.97,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.05,
        )
        assert result.decision == GateDecision.NEEDS_REVIEW

    def test_gate_result_details(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.85,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert "checks" in result.details
        assert "improvement" in result.details
        assert len(result.details["checks"]) >= 3

    def test_no_critical_cases_by_default(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.6,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.05,
        )
        # Without critical_case_ids, critical check always passes
        critical_check = [
            c for c in result.details["checks"] if c["check"] == "critical_cases"
        ][0]
        assert critical_check["passed"] is True
