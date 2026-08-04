"""Tests for validation comparison module."""

import json

import pytest

from pipeline.baseline import BaselineResult
from pipeline.config import load_pipeline_config
from pipeline.validate import (
    ValidationDelta,
    ValidationResult,
    run_validation_fake,
    run_validation_trace,
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


class TestRunValidationTraceOverfitGuard:
    """run_validation_trace 在 overfit 场景的边界数据上应显式报错而非误 ACCEPT。"""

    def _write(self, path, cases):
        path.write_text(json.dumps({"eval_set_id": "t", "eval_cases": cases}))

    def _baseline_val(self):
        return BaselineResult(
            evalset_id="val", pass_rate=1.0, total_cases=0,
            passed_cases=0, failed_cases=0, per_case_results=[],
        )

    def test_overfit_empty_val_raises(self, tmp_path):
        """空 val 集：overfit 无法演示退化 → 显式 ValueError（避免误 ACCEPT）。"""
        train = tmp_path / "train.evalset.json"
        val = tmp_path / "val.evalset.json"
        self._write(train, [{"eval_id": "c1", "conversation": [], "actual_conversation": []}])
        self._write(val, [])
        opt = type("R", (), {"candidate_strategy": "overfit"})()

        with pytest.raises(ValueError, match="overfit scenario requires"):
            run_validation_trace(
                str(train), str(val), self._baseline_val(), opt,
                load_pipeline_config(), scenario="overfit",
            )

    def test_overfit_no_regression_cases_raises(self, tmp_path):
        """未指定回归 case 且 val 集为空时同样报错。"""
        train = tmp_path / "train.evalset.json"
        val = tmp_path / "val.evalset.json"
        self._write(train, [{"eval_id": "c1", "conversation": [], "actual_conversation": []}])
        self._write(val, [])
        opt = type("R", (), {"candidate_strategy": "overfit"})()

        with pytest.raises(ValueError, match="overfit scenario requires"):
            run_validation_trace(
                str(train), str(val), self._baseline_val(), opt,
                load_pipeline_config(), scenario="overfit", val_regression_cases=[],
            )

    def test_overfit_specified_unperturbable_case_raises(self, tmp_path):
        """用户显式指定不可扰动的回归 case → 指向性 ValueError（根因不被笼统
        scenario_error 淹没）。"""
        train = tmp_path / "train.evalset.json"
        val = tmp_path / "val.evalset.json"
        self._write(train, [{"eval_id": "c1", "conversation": [], "actual_conversation": []}])
        # val case 有 conversation 但无 final_response.parts → 不可扰动
        self._write(val, [{"eval_id": "c1", "conversation": [{"text": "hi"}]}])
        opt = type("R", (), {"candidate_strategy": "overfit"})()

        with pytest.raises(ValueError, match="not perturbable"):
            run_validation_trace(
                str(train), str(val), self._baseline_val(), opt,
                load_pipeline_config(), scenario="overfit", val_regression_cases=["c1"],
            )
