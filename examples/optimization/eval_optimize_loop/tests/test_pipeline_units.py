# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for the pure decision functions of the eval_optimize_loop example.

Covered surfaces: failure attribution (``classify_tool_failure`` /
``failure_types_for``), the fake judge rubric (``rubric_score``), the acceptance
gate (``gate_decision``), per-case diffing (``diff_cases``), failure clustering
(``attribute_failures``), and config validation (``validate_config``). All of
them are IO-free, so no model, network, or SDK service is involved.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


_EXAMPLE_ROOT = Path(__file__).resolve().parent.parent

# Load run.py under a unique module name so pytest runs across multiple
# examples cannot collide on a shared "run" module. The module must be in
# sys.modules before exec_module: @dataclass resolves its owning module there.
_SPEC = importlib.util.spec_from_file_location("eval_optimize_loop_run", _EXAMPLE_ROOT / "run.py")
run = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run
_SPEC.loader.exec_module(run)


GATE_CFG: dict[str, Any] = {
    "gate": {
        "min_val_score_gain": 0.1,
        "reject_on_new_hard_fail": True,
        "hard_fail_threshold": 0.6,
        "reject_on_critical_regression": True,
        "reject_overfit_train_up_val_down": True,
        "max_cost_usd": 0.05,
    }
}


def make_case(score: float = 1.0, passed: bool = True, hard_fail: bool = False, key: bool = False) -> dict[str, Any]:
    return {"score": score, "passed": passed, "hard_fail": hard_fail, "key": key, "failure_types": []}


def make_result(mean_score: float, cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"mean_score": mean_score, "cases": cases}


class TestClassifyToolFailure:
    def test_matching_trajectory_returns_none(self):
        tools = [{"name": "lookup_order", "args": {"order_id": "A100"}}]
        assert run.classify_tool_failure(tools, list(tools), {}) is None

    def test_missing_authoritative_tool_is_knowledge_recall(self):
        actual = [{"name": "web_search", "args": {"query": "warranty"}}]
        expected = [{"name": "search_policy", "args": {"topic": "warranty"}}]
        meta = {"authoritative_tool": "search_policy"}
        assert run.classify_tool_failure(actual, expected, meta) == "knowledge_recall_insufficient"

    def test_superset_of_expected_calls_is_spurious(self):
        expected = [{"name": "lookup_order", "args": {"order_id": "A200"}}]
        actual = expected + [{"name": "search_policy", "args": {"topic": "order A200"}}]
        assert run.classify_tool_failure(actual, expected, {}) == "spurious_tool_call"

    def test_extra_call_when_none_expected_is_spurious(self):
        actual = [{"name": "search_policy", "args": {"topic": "thanks"}}]
        assert run.classify_tool_failure(actual, [], {}) == "spurious_tool_call"

    def test_same_leading_tool_with_different_args_is_parameter_error(self):
        actual = [{"name": "lookup_order", "args": {"order_id": "A999"}}]
        expected = [{"name": "lookup_order", "args": {"order_id": "A100"}}]
        assert run.classify_tool_failure(actual, expected, {}) == "parameter_error"

    def test_wrong_tool_is_tool_call_error(self):
        actual = [{"name": "web_search", "args": {}}]
        expected = [{"name": "lookup_order", "args": {"order_id": "A100"}}]
        assert run.classify_tool_failure(actual, expected, {}) == "tool_call_error"

    def test_missing_call_is_tool_call_error(self):
        expected = [{"name": "lookup_order", "args": {"order_id": "A100"}}]
        assert run.classify_tool_failure([], expected, {}) == "tool_call_error"


class TestRubricScore:
    def test_json_format_accepts_json_object(self):
        assert run.rubric_score({"rubric": "json_format"}, {"text": '{"status": "ok"}', "tools": []}) == 1.0

    def test_json_format_rejects_plain_text(self):
        assert run.rubric_score({"rubric": "json_format"}, {"text": "status ok", "tools": []}) == 0.0

    def test_json_format_rejects_bare_scalar(self):
        assert run.rubric_score({"rubric": "json_format"}, {"text": "123", "tools": []}) == 0.0

    def test_no_tool_rubric(self):
        assert run.rubric_score({"rubric": "no_tool"}, {"text": "hi", "tools": []}) == 1.0
        assert run.rubric_score({"rubric": "no_tool"}, {"text": "hi", "tools": [{"name": "x", "args": {}}]}) == 0.0

    def test_single_tool_rubric(self):
        one = [{"name": "a", "args": {}}]
        assert run.rubric_score({"rubric": "single_tool"}, {"text": "", "tools": one}) == 1.0
        assert run.rubric_score({"rubric": "single_tool"}, {"text": "", "tools": one * 2}) == 0.5

    def test_unset_rubric_defaults_to_full_score(self):
        assert run.rubric_score({}, {"text": "anything", "tools": []}) == 1.0


class TestFailureTypesFor:
    def test_collects_and_deduplicates_labels(self):
        expected = [{"name": "lookup_order", "args": {"order_id": "A100"}}]
        output = {"text": "no idea", "tools": []}
        labels = run.failure_types_for({"rubric": "json_format"}, 0.0, 0.0, 0.0, output, expected)
        assert labels == ["final_response_mismatch", "tool_call_error", "format_error"]

    def test_all_dimensions_passing_yields_no_labels(self):
        assert run.failure_types_for({}, 1.0, 1.0, 1.0, {"text": "", "tools": []}, []) == []


class TestGateDecision:
    def run_gate(
        self,
        base_train: float = 0.5,
        cand_train: float = 0.5,
        baseline_val_cases: dict[str, dict[str, Any]] | None = None,
        candidate_val_cases: dict[str, dict[str, Any]] | None = None,
        base_val: float = 0.5,
        cand_val: float = 0.7,
        cost_usd: float = 0.0,
    ) -> dict[str, Any]:
        baseline_val_cases = baseline_val_cases or {"c1": make_case()}
        candidate_val_cases = candidate_val_cases or {"c1": make_case()}
        baseline_val = make_result(base_val, baseline_val_cases)
        candidate_val = make_result(cand_val, candidate_val_cases)
        val_delta = run.diff_cases(baseline_val, candidate_val)
        return run.gate_decision(
            make_result(base_train, {}),
            make_result(cand_train, {}),
            baseline_val,
            candidate_val,
            val_delta,
            GATE_CFG,
            cost_usd,
        )

    def failed_names(self, gate: dict[str, Any]) -> set[str]:
        return {check["name"] for check in gate["checks"] if not check["passed"]}

    def test_accepts_when_all_checks_pass(self):
        gate = self.run_gate()
        assert gate["decision"] == "ACCEPT"
        assert gate["accepted"] is True
        assert self.failed_names(gate) == set()

    def test_rejects_insufficient_validation_gain(self):
        gate = self.run_gate(cand_val=0.55)
        assert gate["decision"] == "REJECT"
        assert self.failed_names(gate) == {"validation_gain_threshold"}

    def test_rejects_new_hard_fail(self):
        gate = self.run_gate(
            baseline_val_cases={"c1": make_case(score=0.9)},
            candidate_val_cases={"c1": make_case(score=0.3, passed=False, hard_fail=True)},
        )
        assert "no_new_hard_fail" in self.failed_names(gate)

    def test_rejects_key_case_regression(self):
        gate = self.run_gate(
            baseline_val_cases={"c1": make_case(score=1.0, key=True)},
            candidate_val_cases={"c1": make_case(score=0.9, key=True)},
        )
        assert "no_critical_regression" in self.failed_names(gate)

    def test_non_key_case_regression_is_not_critical(self):
        gate = self.run_gate(
            baseline_val_cases={"c1": make_case(score=1.0)},
            candidate_val_cases={"c1": make_case(score=0.9)},
        )
        assert "no_critical_regression" not in self.failed_names(gate)

    def test_rejects_train_up_validation_down_overfit(self):
        gate = self.run_gate(base_train=0.3, cand_train=0.8, base_val=0.7, cand_val=0.6)
        assert "not_overfit_train_up_val_down" in self.failed_names(gate)

    def test_rejects_cost_over_budget(self):
        gate = self.run_gate(cost_usd=1.0)
        assert self.failed_names(gate) == {"cost_budget"}


class TestDiffCases:
    def test_all_delta_kinds(self):
        baseline = make_result(
            0.5,
            {
                "new_pass": make_case(score=0.4, passed=False),
                "new_fail": make_case(score=0.9, passed=True),
                "score_up": make_case(score=0.4, passed=False),
                "score_down": make_case(score=0.9, passed=True),
                "same": make_case(score=0.7, passed=True),
            },
        )
        candidate = make_result(
            0.5,
            {
                "new_pass": make_case(score=0.9, passed=True),
                "new_fail": make_case(score=0.4, passed=False),
                "score_up": make_case(score=0.5, passed=False),
                "score_down": make_case(score=0.8, passed=True),
                "same": make_case(score=0.7, passed=True),
            },
        )
        delta = run.diff_cases(baseline, candidate)
        assert {case_id: item["kind"] for case_id, item in delta.items()} == {
            "new_pass": "new_pass",
            "new_fail": "new_fail",
            "score_up": "score_up",
            "score_down": "score_down",
            "same": "same",
        }
        assert delta["score_up"]["delta"] == 0.1

    def test_mismatched_case_ids_abort_with_readable_error(self):
        baseline = make_result(1.0, {"a": make_case()})
        candidate = make_result(1.0, {"b": make_case()})
        with pytest.raises(SystemExit, match="different case ids"):
            run.diff_cases(baseline, candidate)


class TestAttributeFailures:
    def test_counts_failures_and_skips_passed_cases(self):
        passed = make_case()
        failed = dict(make_case(score=0.2, passed=False), failure_types=["tool_call_error", "format_error"])
        unknown = dict(make_case(score=0.2, passed=False), failure_types=[])
        result_a = make_result(0.5, {"ok": passed, "bad": failed})
        result_b = make_result(0.5, {"mystery": unknown})
        clustered = run.attribute_failures(result_a, result_b)
        assert clustered["counts"] == {"tool_call_error": 1, "format_error": 1, "unknown": 1}
        assert clustered["by_case"] == {"bad": ["tool_call_error", "format_error"], "mystery": ["unknown"]}


class TestAttributionSelfCheck:
    def test_accuracy_against_expected_categories(self):
        failures = {
            "by_case": {
                "hit": ["tool_call_error", "final_response_mismatch"],
                "miss": ["parameter_error"],
                "unlabeled": ["format_error"],
            }
        }
        case_meta = {
            "hit": {"category": "tool_call_error"},
            "miss": {"category": "knowledge_recall_insufficient"},
            "unlabeled": {},
        }
        check = run.attribution_self_check(failures, case_meta)
        assert check["cases_with_expected_category"] == 2
        assert check["matched"] == 1
        assert check["accuracy"] == 0.5
        assert check["by_case"]["hit"]["matched"] is True
        assert check["by_case"]["miss"]["matched"] is False
        assert "unlabeled" not in check["by_case"]

    def test_no_expected_categories_yields_null_accuracy(self):
        check = run.attribution_self_check({"by_case": {"a": ["x"]}}, {"a": {}})
        assert check["accuracy"] is None
        assert check["cases_with_expected_category"] == 0

    def test_shipped_sample_attribution_is_fully_consistent(self):
        """The bundled 6-case sample must self-attribute at 100% accuracy."""
        cfg = run.load_json(_EXAMPLE_ROOT / "optimizer.json")
        case_meta = {
            key: value
            for key, value in run.load_json(_EXAMPLE_ROOT / "case_meta.json").items()
            if not key.startswith("_")
        }
        prompt_path = _EXAMPLE_ROOT / "prompts" / "system.md"

        async def evaluate() -> dict[str, Any]:
            train = run.validate_evalset(_EXAMPLE_ROOT / "train.evalset.json")
            val = run.validate_evalset(_EXAMPLE_ROOT / "val.evalset.json")
            baseline_train = await run.evaluate_evalset(train, prompt_path, cfg, case_meta, "fake")
            baseline_val = await run.evaluate_evalset(val, prompt_path, cfg, case_meta, "fake")
            return run.attribute_failures(baseline_train, baseline_val)

        import asyncio

        failures = asyncio.run(evaluate())
        check = run.attribution_self_check(failures, case_meta)
        assert check["cases_with_expected_category"] == 4
        assert check["accuracy"] == 1.0


class TestValidateConfig:
    @pytest.fixture()
    def valid_cfg(self) -> dict[str, Any]:
        return copy.deepcopy(run.load_json(_EXAMPLE_ROOT / "optimizer.json"))

    def test_shipped_config_is_valid(self, valid_cfg: dict[str, Any]):
        run.validate_config(valid_cfg)

    def test_rejects_missing_gate_key(self, valid_cfg: dict[str, Any]):
        del valid_cfg["gate"]["max_cost_usd"]
        with pytest.raises(SystemExit, match="max_cost_usd"):
            run.validate_config(valid_cfg)

    def test_rejects_weights_not_summing_to_one(self, valid_cfg: dict[str, Any]):
        valid_cfg["evaluate"]["metrics"][0]["weight"] = 0.9
        with pytest.raises(SystemExit, match="sum to 1.0"):
            run.validate_config(valid_cfg)

    def test_rejects_unexpected_metric_names(self, valid_cfg: dict[str, Any]):
        valid_cfg["evaluate"]["metrics"][0]["name"] = "latency"
        with pytest.raises(SystemExit, match="must define exactly"):
            run.validate_config(valid_cfg)

    def test_rejects_out_of_range_threshold(self, valid_cfg: dict[str, Any]):
        valid_cfg["evaluate"]["pass_threshold"] = 1.5
        with pytest.raises(SystemExit, match="within \\[0, 1\\]"):
            run.validate_config(valid_cfg)
