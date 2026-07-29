#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""G1-G7 gate 单元测试。纯规则，无需 SDK。"""

from __future__ import annotations

import gates


def _case(
    eval_id="c1",
    split="val",
    slice_name="math",
    risk_level="low",
    protected=False,
    champion_status="PASSED",
    challenger_status="PASSED",
    champion_score=1.0,
    challenger_score=1.0,
):
    return gates.CaseDelta(
        eval_id=eval_id,
        split=split,
        slice_name=slice_name,
        risk_level=risk_level,
        protected=protected,
        champion_status=champion_status,
        challenger_status=challenger_status,
        champion_score=champion_score,
        challenger_score=challenger_score,
    )


def test_g1_accept_when_val_lift_meets_threshold() -> None:
    cases = [_case(champion_score=0.5, challenger_score=0.8)]
    d = gates.evaluate(cases)
    assert d.accepted
    assert "G1" not in d.violated


def test_g1_reject_when_val_lift_insufficient() -> None:
    cases = [_case(champion_score=0.5, challenger_score=0.51)]
    d = gates.evaluate(cases, config=gates.GateConfig(min_val_lift=0.02))
    assert not d.accepted
    assert "G1" in d.violated


def test_g2_overfit_train_up_val_down_rejected() -> None:
    train = [
        _case("t1", split="train", champion_score=0.3, challenger_score=0.9),
        _case("t2", split="train", champion_score=0.3, challenger_score=0.9),
    ]
    val = [
        _case("v1", champion_score=0.7, challenger_score=0.4),
    ]
    d = gates.evaluate(train + val)
    assert "G2" in d.violated
    assert not d.accepted
    # 理由中必须提到过拟合
    assert any("过拟合" in r for r in d.reasons)


def test_g2_not_triggered_when_both_improve() -> None:
    train = [_case("t", split="train", champion_score=0.3, challenger_score=0.9)]
    val = [_case("v", champion_score=0.5, challenger_score=0.8)]
    d = gates.evaluate(train + val)
    assert "G2" not in d.violated


def test_g3_new_hard_fail_rejected() -> None:
    cases = [
        _case(
            eval_id="h",
            risk_level="high",
            champion_status="PASSED",
            challenger_status="FAILED",
            champion_score=1.0,
            challenger_score=0.0,
        ),
        # 必须有 val lift，否则会先被 G1 卡掉，看不出 G3
        _case(eval_id="v", champion_score=0.0, challenger_score=1.0),
    ]
    d = gates.evaluate(cases)
    assert "G3" in d.violated


def test_g3_high_risk_but_already_failing_not_counted() -> None:
    cases = [
        _case(
            eval_id="h",
            risk_level="high",
            champion_status="FAILED",
            challenger_status="FAILED",
            champion_score=0.0,
            challenger_score=0.0,
        ),
        _case(eval_id="v", champion_score=0.0, challenger_score=1.0),
    ]
    d = gates.evaluate(cases)
    assert "G3" not in d.violated


def test_g4_protected_regression_rejected() -> None:
    cases = [
        _case(
            eval_id="p",
            protected=True,
            champion_score=1.0,
            challenger_score=0.5,
        ),
        _case(eval_id="v", champion_score=0.0, challenger_score=1.0),
    ]
    d = gates.evaluate(cases)
    assert "G4" in d.violated


def test_g5_slice_regression_rejected() -> None:
    cases = [
        _case("a1", slice_name="alpha", champion_score=1.0, challenger_score=1.0),
        _case(
            "a2",
            slice_name="beta",
            champion_score=1.0,
            challenger_score=0.0,
        ),
        # 整体 val lift 必须够，否则会被 G1 拦下
        _case("a3", slice_name="gamma", champion_score=0.0, challenger_score=1.0),
        _case("a4", slice_name="gamma", champion_score=0.0, challenger_score=1.0),
    ]
    d = gates.evaluate(cases, config=gates.GateConfig(slice_tolerance=0.05))
    assert "G5" in d.violated


def test_g6_cost_unavailable_rejected() -> None:
    cases = [_case(champion_score=0.0, challenger_score=1.0)]
    d = gates.evaluate(cases, cost_status="unavailable")
    assert "G6" in d.violated


def test_g6_cost_over_budget_rejected() -> None:
    cases = [_case(champion_score=0.0, challenger_score=1.0)]
    d = gates.evaluate(
        cases,
        cost_status="measured",
        total_tokens=10_000_000,
        config=gates.GateConfig(budget_tokens=100_000),
    )
    assert "G6" in d.violated


def test_g6_usd_cost_over_budget_rejected() -> None:
    cases = [_case(champion_score=0.0, challenger_score=1.0)]
    decision = gates.evaluate(
        cases,
        cost_status="measured",
        total_tokens=100,
        total_cost=1.5,
        config=gates.GateConfig(budget_usd=1.0),
    )
    assert "G6" in decision.violated


def test_g6_measured_with_missing_value_rejected() -> None:
    cases = [_case(champion_score=0.0, challenger_score=1.0)]
    decision = gates.evaluate(
        cases,
        cost_status="measured",
        total_tokens=None,
        total_cost=None,
    )
    assert "G6" in decision.violated


def test_g6_cost_measured_zero_passes() -> None:
    cases = [_case(champion_score=0.0, challenger_score=1.0)]
    d = gates.evaluate(cases, cost_status="measured", total_tokens=0)
    assert "G6" not in d.violated


def test_g7_epsilon_guard() -> None:
    """val_delta 接近 0 时，即使 G1-G6 全过，G7 也拦下。"""
    cases = [
        _case(champion_score=1.0, challenger_score=1.0001),
    ]
    d = gates.evaluate(
        cases,
        cost_status="measured",
        config=gates.GateConfig(min_val_lift=0.0, epsilon=0.001),
    )
    assert "G7" in d.violated


def test_all_pass_accept() -> None:
    cases = [
        _case(eval_id="t1", split="train", champion_score=0.4, challenger_score=0.9),
        _case(eval_id="v1", champion_score=0.4, challenger_score=0.9),
    ]
    d = gates.evaluate(cases)
    assert d.accepted
    assert d.violated == []
