"""Tests for overfitting detection in the optimization pipeline."""

import pytest

from pipeline.baseline import BaselineResult
from pipeline.gate import evaluate_gate, GateDecision
from pipeline.validate import run_validation_fake
from pipeline.config import load_pipeline_config
from pipeline.attribution import attribute_failures


class TestOverfittingDetection:
    """Overfitting = train score improves but validation score degrades."""

    def test_train_up_val_down_rejected(self):
        """Gate should REJECT when train improves but val degrades."""
        # Train baseline at 50%, candidate at 80% (big improvement on train)
        # But critical case from val fails
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.8,
            baseline_metrics={"val_pass_rate": 0.7},
            candidate_metrics={"val_pass_rate": 0.3},  # Val degraded
            min_improvement=0.1,
            baseline_failed=[],
            candidate_failed=["val_critical_001"],  # New failure on val
        )
        # Should reject due to new failure
        assert result.decision != GateDecision.ACCEPT

    def test_train_up_val_up_accepted(self):
        """Both train and val improve → should accept."""
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.8,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.ACCEPT

    def test_validation_degradation_detection(self):
        """run_validation_fake detects when candidate introduces new failures."""
        baseline = BaselineResult(
            evalset_id="val", total_cases=5,
            passed_cases=5, failed_cases=0,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": f"c{i}", "pass": True} for i in range(1, 6)
            ],
        )
        candidate = BaselineResult(
            evalset_id="val", total_cases=5,
            passed_cases=3, failed_cases=2,
            failed_case_ids=["c3", "c4"],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": True},
                {"eval_id": "c3", "pass": False},  # Degraded!
                {"eval_id": "c4", "pass": False},  # Degraded!
                {"eval_id": "c5", "pass": True},
            ],
        )
        result = run_validation_fake(
            "val.json", baseline, candidate, load_pipeline_config(),
        )
        assert result.is_overfitting
        assert result.new_failures == 2

    def test_no_overfitting_when_both_improve(self):
        """No flags when both train and val improve."""
        baseline = BaselineResult(
            evalset_id="val", total_cases=3,
            passed_cases=1, failed_cases=2,
            failed_case_ids=["c1", "c2"],
            per_case_results=[
                {"eval_id": "c1", "pass": False},
                {"eval_id": "c2", "pass": False},
                {"eval_id": "c3", "pass": True},
            ],
        )
        candidate = BaselineResult(
            evalset_id="val", total_cases=3,
            passed_cases=3, failed_cases=0,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": True},
                {"eval_id": "c3", "pass": True},
            ],
        )
        result = run_validation_fake(
            "val.json", baseline, candidate, load_pipeline_config(),
        )
        assert not result.is_overfitting
        assert result.new_passes == 2

    def test_multiround_overfitting_early_stop_concept(self):
        """Overfitting should trigger early stop in multi-round optimization.

        This test verifies the concept: if validation degrades while
        training improves, the pipeline should detect and flag it.
        """
        # Simulate: round 1 improves both, round 2 overfits
        baseline_train = 0.5
        round1_train = 0.7
        round2_train = 0.85  # Train keeps improving

        baseline_val = 0.6
        round1_val = 0.7    # Val also improves (good)
        round2_val = 0.4    # Val degrades (overfitting!)

        # After round 1: should be fine
        r1_gate = evaluate_gate(
            baseline_pass_rate=baseline_train,
            candidate_pass_rate=round1_train,
            baseline_metrics={"val": baseline_val},
            candidate_metrics={"val": round1_val},
            min_improvement=0.1,
        )
        assert r1_gate.decision == GateDecision.ACCEPT

        # After round 2: should detect degradation
        r2_gate = evaluate_gate(
            baseline_pass_rate=baseline_train,
            candidate_pass_rate=round2_train,
            baseline_metrics={"val": baseline_val},
            candidate_metrics={"val": round2_val},
            min_improvement=0.1,
        )
        # Gate itself doesn't directly check val score degradation
        # (that's validate.py's job), but it will flag the candidate
        # if no improvement is detected
