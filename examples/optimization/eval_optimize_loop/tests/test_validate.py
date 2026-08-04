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

    def test_candidate_val_empty_raises_not_fabricated_regression(self, tmp_path):
        """baseline val 有已评测 case 而候选 val 无评测结果 → 显式报错，而非把
        baseline 通过 case 全判 new_fail 触发误过拟合 REJECT（reviewer Critical）。"""
        train = tmp_path / "train.evalset.json"
        val = tmp_path / "val.evalset.json"
        self._write(train, [{"eval_id": "c1", "conversation": [], "actual_conversation": []}])
        self._write(val, [])  # val 集空 → 候选 val 无任何已评测 case
        opt = type("R", (), {"candidate_strategy": "noop"})()
        bl = BaselineResult(
            evalset_id="val", pass_rate=1.0, total_cases=1,
            passed_cases=1, failed_cases=0,
            per_case_results=[{"eval_id": "c1", "pass": True}],
        )

        with pytest.raises(ValueError, match="no evaluated cases"):
            run_validation_trace(
                str(train), str(val), bl, opt,
                load_pipeline_config(), scenario="noop",
            )

    def test_noop_ignores_candidate_conversation(self, tmp_path):
        """noop 场景：case 携带 candidate_conversation 也不覆盖，候选与 baseline
        一致、delta 全 unchanged（reviewer Warning：覆盖破坏 noop 语义）。"""
        train = tmp_path / "train.evalset.json"
        val = tmp_path / "val.evalset.json"
        conv = [{"invocation_id": "i1",
                 "user_content": {"parts": [{"text": "q"}], "role": "user"},
                 "final_response": {"parts": [{"text": "a"}], "role": "model"}}]
        case = {"eval_id": "c1", "conversation": conv,
                "actual_conversation": conv,
                # 与 actual_conversation 不同的 candidate_conversation：noop 下不应回放
                "candidate_conversation": [{"invocation_id": "i1",
                                            "user_content": {"parts": [{"text": "q"}], "role": "user"},
                                            "final_response": {"parts": [{"text": "WRONG"}], "role": "model"}}]}
        self._write(train, [{"eval_id": "c1", "conversation": [], "actual_conversation": []}])
        self._write(val, [case])
        opt = type("R", (), {"candidate_strategy": "noop"})()
        bl = BaselineResult(
            evalset_id="val", pass_rate=1.0, total_cases=1,
            passed_cases=1, failed_cases=0,
            per_case_results=[{"eval_id": "c1", "pass": True}],
        )

        result = run_validation_trace(
            str(train), str(val), bl, opt,
            load_pipeline_config(), scenario="noop",
        )
        assert all(d.change == "unchanged" for d in result.deltas)
        assert result.new_failures == 0

    def test_overfit_non_string_eval_id_selection(self, tmp_path):
        """overfit 自动选回归 case：val 集含非字符串 eval_id(整数) 时，
        str() 归一化一致、不误抛 'no perturbable case'（reviewer Warning）。"""
        train = tmp_path / "train.evalset.json"
        val = tmp_path / "val.evalset.json"
        conv = [{"invocation_id": "i1",
                 "user_content": {"parts": [{"text": "q"}], "role": "user"},
                 "final_response": {"parts": [{"text": "a"}], "role": "model"}}]
        # int eval_id + 可扰动 + baseline 通过
        self._write(train, [{"eval_id": "c1", "conversation": [], "actual_conversation": []}])
        self._write(val, [{"eval_id": 1, "conversation": conv, "actual_conversation": conv}])
        opt = type("R", (), {"candidate_strategy": "overfit"})()
        bl = BaselineResult(
            evalset_id="val", pass_rate=1.0, total_cases=1,
            passed_cases=1, failed_cases=0,
            per_case_results=[{"eval_id": 1, "pass": True}],
        )

        result = run_validation_trace(
            str(train), str(val), bl, opt,
            load_pipeline_config(), scenario="overfit",
        )
        # 自动选出 int id 的 case 扰动 → 产生 val 回归（而非误抛空集错误）
        assert result.new_failures >= 1
