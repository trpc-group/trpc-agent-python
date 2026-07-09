"""Tests for validation comparison module."""

import pytest

from pipeline.baseline import BaselineResult
from pipeline.config import load_pipeline_config
from pipeline.validate import (
    ValidationDelta,
    ValidationResult,
    run_validation_fake,
)


class TestValidationDelta:
    """Tests for ValidationDelta dataclass."""

    def test_new_pass_change(self):
        delta = ValidationDelta(
            eval_id="c1", baseline_passed=False, candidate_passed=True,
            change="new_pass",
        )
        assert delta.change == "new_pass"

    def test_new_fail_change(self):
        delta = ValidationDelta(
            eval_id="c1", baseline_passed=True, candidate_passed=False,
            change="new_fail",
        )
        assert delta.change == "new_fail"


class TestValidationResult:
    """Tests for ValidationResult properties."""

    def test_new_passes_count(self):
        result = ValidationResult(deltas=[
            ValidationDelta("c1", False, True, "new_pass"),
            ValidationDelta("c2", True, True, "unchanged"),
        ])
        assert result.new_passes == 1

    def test_new_failures_count(self):
        result = ValidationResult(deltas=[
            ValidationDelta("c1", True, False, "new_fail"),
            ValidationDelta("c2", True, False, "new_fail"),
            ValidationDelta("c3", True, True, "unchanged"),
        ])
        assert result.new_failures == 2

    def test_unchanged_count(self):
        result = ValidationResult(deltas=[
            ValidationDelta("c1", True, True, "unchanged"),
            ValidationDelta("c2", False, False, "unchanged"),
        ])
        assert result.unchanged == 2

    def test_is_overfitting(self):
        result = ValidationResult(deltas=[
            ValidationDelta("c1", True, False, "new_fail"),
        ])
        assert result.is_overfitting is True

    def test_is_not_overfitting(self):
        result = ValidationResult(deltas=[
            ValidationDelta("c1", False, True, "new_pass"),
            ValidationDelta("c2", True, True, "unchanged"),
        ])
        assert result.is_overfitting is False


class TestRunValidationFake:
    """Tests for run_validation_fake()."""

    def test_new_pass_tracking(self):
        baseline = BaselineResult(
            evalset_id="test", total_cases=2,
            passed_cases=1, failed_cases=1,
            failed_case_ids=["c1"],
            per_case_results=[
                {"eval_id": "c1", "pass": False},
                {"eval_id": "c2", "pass": True},
            ],
        )
        candidate = BaselineResult(
            evalset_id="test", total_cases=2,
            passed_cases=2, failed_cases=0,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": True},
            ],
        )
        result = run_validation_fake(
            "fake.json", baseline, candidate, load_pipeline_config(),
        )
        assert result.new_passes == 1
        assert result.new_failures == 0

    def test_new_failure_tracking(self):
        baseline = BaselineResult(
            evalset_id="test", total_cases=2,
            passed_cases=2, failed_cases=0,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": True},
            ],
        )
        candidate = BaselineResult(
            evalset_id="test", total_cases=2,
            passed_cases=1, failed_cases=1,
            failed_case_ids=["c1"],
            per_case_results=[
                {"eval_id": "c1", "pass": False},
                {"eval_id": "c2", "pass": True},
            ],
        )
        result = run_validation_fake(
            "fake.json", baseline, candidate, load_pipeline_config(),
        )
        assert result.new_failures == 1

    def test_overfitting_detection(self):
        baseline = BaselineResult(
            evalset_id="test", total_cases=5,
            passed_cases=5, failed_cases=0,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": f"c{i}", "pass": True} for i in range(1, 6)
            ],
        )
        candidate = BaselineResult(
            evalset_id="test", total_cases=5,
            passed_cases=2, failed_cases=3,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": True},
                {"eval_id": "c3", "pass": False},
                {"eval_id": "c4", "pass": False},
                {"eval_id": "c5", "pass": False},
            ],
        )
        result = run_validation_fake(
            "fake.json", baseline, candidate, load_pipeline_config(),
        )
        assert result.is_overfitting

    def test_empty_validation(self):
        result = run_validation_fake(
            "fake.json",
            BaselineResult(), BaselineResult(),
            load_pipeline_config(),
        )
        assert result.new_passes == 0
        assert result.new_failures == 0

    def test_all_unchanged(self):
        baseline = BaselineResult(
            evalset_id="test", total_cases=3,
            passed_cases=2, failed_cases=1,
            failed_case_ids=["c2"],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": False},
                {"eval_id": "c3", "pass": True},
            ],
        )
        # Candidate identical to baseline
        result = run_validation_fake(
            "fake.json", baseline, baseline, load_pipeline_config(),
        )
        assert result.new_passes == 0
        assert result.new_failures == 0
        assert result.unchanged == 3
