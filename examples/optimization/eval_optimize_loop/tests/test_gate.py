# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Gate decision tests."""

from __future__ import annotations

from pipeline import GateEvaluator
from pipeline.types import GateConfig

from .conftest import delta_from_scores
from .conftest import rule


def test_gate_accepts_validation_improvement_without_regressions() -> None:
    delta = delta_from_scores(
        train_pairs=[("train_new_pass", False, 0.0, True, 1.0)],
        val_pairs=[("val_new_pass", False, 0.0, True, 1.0)],
    )

    decision = GateEvaluator(GateConfig(min_val_score_gain=0.05)).evaluate(delta=delta)

    assert decision.accepted is True
    assert decision.decision == "accept"
    assert decision.summary["val_score_delta"] == 1.0
    assert decision.summary["overfit_detected"] is False


def test_gate_rejects_when_validation_gain_is_below_threshold() -> None:
    delta = delta_from_scores(
        train_pairs=[("train_score_up", True, 0.8, True, 1.0)],
        val_pairs=[("val_score_up", True, 0.95, True, 1.0)],
    )

    decision = GateEvaluator(GateConfig(min_val_score_gain=0.1)).evaluate(delta=delta)

    assert decision.accepted is False
    assert decision.decision == "reject"
    assert rule(decision, "validation_score_gain").passed is False


def test_gate_rejects_new_validation_failure() -> None:
    delta = delta_from_scores(
        train_pairs=[("train_ok", True, 1.0, True, 1.0)],
        val_pairs=[("val_new_fail", True, 1.0, False, 0.0)],
    )

    decision = GateEvaluator(GateConfig(min_val_score_gain=-1.0, allow_new_failures=False)).evaluate(delta=delta)

    assert decision.accepted is False
    assert rule(decision, "new_failures").passed is False
    assert decision.summary["new_fail_count"] == 1


def test_gate_rejects_validation_regression_and_metric_pass_to_fail() -> None:
    delta = delta_from_scores(
        train_pairs=[("train_ok", True, 1.0, True, 1.0)],
        val_pairs=[("val_score_down", True, 1.0, True, 0.5)],
    )

    decision = GateEvaluator(GateConfig(min_val_score_gain=-1.0, allow_regressions=False)).evaluate(delta=delta)

    assert decision.accepted is False
    assert rule(decision, "validation_regressions").passed is False
    assert decision.summary["regression_count"] == 1


def test_gate_rejects_critical_case_regression() -> None:
    delta = delta_from_scores(
        train_pairs=[("train_ok", True, 1.0, True, 1.0)],
        val_pairs=[("critical_case", True, 1.0, True, 0.5)],
    )

    decision = GateEvaluator(
        GateConfig(
            min_val_score_gain=-1.0,
            allow_regressions=True,
            critical_case_ids=["critical_case"],
        )).evaluate(delta=delta)

    assert decision.accepted is False
    assert rule(decision, "critical_case_regressions").passed is False
    assert decision.summary["critical_regression_count"] == 1


def test_gate_rejects_train_gain_with_validation_drop_as_overfit() -> None:
    delta = delta_from_scores(
        train_pairs=[("train_new_pass", False, 0.0, True, 1.0)],
        val_pairs=[("val_score_down", True, 1.0, True, 0.5)],
    )

    decision = GateEvaluator(
        GateConfig(
            min_val_score_gain=-1.0,
            allow_regressions=True,
            overfit_policy={
                "enabled": True,
                "train_gain_min": 0.05,
                "val_drop_tolerance": 0.0,
            },
        )).evaluate(delta=delta)

    assert decision.accepted is False
    assert decision.summary["overfit_detected"] is True
    assert rule(decision, "overfit_detection").passed is False


def test_gate_rejects_cost_budget_overrun() -> None:
    delta = delta_from_scores(
        train_pairs=[("train_ok", True, 1.0, True, 1.0)],
        val_pairs=[("val_ok", True, 0.8, True, 1.0)],
        baseline_cost=1.0,
        candidate_cost=2.0,
    )

    decision = GateEvaluator(GateConfig(max_cost_delta=0.1, max_cost_ratio=1.2)).evaluate(delta=delta)

    assert decision.accepted is False
    assert rule(decision, "cost_budget").passed is False
    assert decision.summary["cost_delta"] > 0.1
