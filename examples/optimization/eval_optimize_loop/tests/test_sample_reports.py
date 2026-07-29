# -*- coding: utf-8 -*-
"""Guard committed sample reports against code/report drift."""

import json
from pathlib import Path

import pytest


EXAMPLE_DIR = Path(__file__).resolve().parents[1]
EXPECTED_CHECKS = {
    "min_improvement",
    "no_hard_regression",
    "key_cases_ok",
    "cost_ok",
    "per_metric_floor",
    "no_overfit",
}


@pytest.mark.parametrize("mode", ["trace_mode", "live_mode"])
def test_sample_report_gate_and_overfit_fields_are_self_consistent(mode):
    report_path = EXAMPLE_DIR / "output" / mode / "optimization_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    checks = report["gate_decision"]["checks"]
    assert set(checks) == EXPECTED_CHECKS
    assert report["gate_decision"]["accepted"] is all(checks.values())

    validation = report["candidate_validation"]
    expected_overfit = (
        validation["train"]["delta"] >= 0.01
        and validation["val"]["delta"] <= -0.001
    )
    assert validation["overfit_summary"]["is_overfit"] is expected_overfit
    assert checks["no_overfit"] is (not expected_overfit)

    optimization = report["optimization"]
    assert optimization["total_rounds"] == len(optimization["rounds"])
    assert optimization["accepted_rounds"] == sum(
        item["accepted"] for item in optimization["rounds"]
    )


@pytest.mark.parametrize("mode", ["trace_mode", "live_mode"])
def test_newly_failing_overgeneralization_text_matches_reporter(mode):
    report_path = EXAMPLE_DIR / "output" / mode / "optimization_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    for case in report["candidate_validation"]["val"]["per_case"]:
        if case["change_type"] != "newly_failing":
            continue
        expected_tools = {item["name"] for item in case["expected_tools"]}
        candidate_tools = {item["name"] for item in case["candidate_tools"]}
        if not expected_tools or not expected_tools.issubset(candidate_tools):
            continue
        if len(candidate_tools) <= len(expected_tools):
            continue

        extra = sorted(candidate_tools - expected_tools)
        expected_reason = (
            f"过度泛化：Prompt 导致 Agent 在仅需 {sorted(expected_tools)} 的场景下"
            f"额外调用了 {extra}。模型过度学习了训练集的模式，"
            f"在不需要时也补充了额外信息。"
        )
        assert case["failure_type"] == "overgeneralization"
        assert case["failure_reason"] == expected_reason


def test_public_trace_cases_cover_required_optimization_outcomes():
    report_path = (
        EXAMPLE_DIR
        / "output"
        / "trace_mode"
        / "optimization_report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cases = [
        case
        for scope in ("train", "val")
        for case in report["candidate_validation"][scope]["per_case"]
    ]

    assert any(case["change_type"] == "newly_passing" for case in cases)
    assert any(
        case["baseline_status"] == "FAILED"
        and case["candidate_status"] == "FAILED"
        and case["change_type"] == "unchanged"
        for case in cases
    )
    assert any(case["change_type"] == "newly_failing" for case in cases)


def test_trace_report_audits_validation_holdout_boundary():
    report_path = (
        EXAMPLE_DIR
        / "output"
        / "trace_mode"
        / "optimization_report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    scope = report["optimization"]["data_scope"]

    assert scope["candidate_generation"] == [
        "baseline_train",
        "train_failure_attribution",
        "train_expected_tool_patterns",
    ]
    assert "baseline_val" in scope["holdout_only"]
    assert "candidate_val" in scope["holdout_only"]
