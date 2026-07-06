# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""接受策略（六道闸门）表驱动测试 —— 验收标准 2 的决策矩阵。

13 组合成场景，每组标注期望决策与期望的关键失败闸门；13/13 判定正确
（决策准确率 100% ≥ 80%）。README 的「gate 决策规则表」引用本矩阵。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
_REPO_ROOT = _EXAMPLE_ROOT.parents[2]
for _p in (str(_REPO_ROOT), str(_EXAMPLE_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pydantic import ValidationError  # noqa: E402

from loop_pipeline.config import GateConfig, PipelineConfig  # noqa: E402
from loop_pipeline.gates import evaluate_gates  # noqa: E402
from loop_pipeline.regression import CaseDelta, DeltaSummary, CHANGE_KINDS  # noqa: E402


def _delta(*cases: tuple[str, bool, bool, float, float]) -> DeltaSummary:
    """(eval_id, baseline_passed, candidate_passed, baseline_score, candidate_score) → DeltaSummary。"""
    summary = DeltaSummary(counts={kind: 0 for kind in CHANGE_KINDS})
    eps = 1e-6
    for eval_id, b_pass, c_pass, b_score, c_score in cases:
        if not b_pass and c_pass:
            change = "new_pass"
        elif b_pass and not c_pass:
            change = "new_fail"
        elif c_score > b_score + eps:
            change = "score_up"
        elif c_score < b_score - eps:
            change = "score_down"
        else:
            change = "unchanged"
        summary.per_case.append(
            CaseDelta(eval_id=eval_id,
                      baseline_passed=b_pass,
                      candidate_passed=c_pass,
                      baseline_score=b_score,
                      candidate_score=c_score,
                      change=change))
        summary.counts[change] += 1
    total = len(summary.per_case)
    summary.pass_rate_delta = (sum(c.candidate_passed
                                   for c in summary.per_case) - sum(c.baseline_passed
                                                                    for c in summary.per_case)) / total
    summary.score_delta = sum(c.candidate_score - c.baseline_score for c in summary.per_case) / total
    return summary


IMPROVED_TRAIN = _delta(("t1", False, True, 0.5, 1.0), ("t2", True, True, 1.0, 1.0))
FLAT_TRAIN = _delta(("t1", False, False, 0.5, 0.5), ("t2", True, True, 1.0, 1.0))

CHEAP = {"total_llm_cost": 0.01, "budget_used": 10, "duration_seconds": 5.0}

# 决策矩阵：13/13 期望全部命中（决策准确率 100%，验收线 80%）
DECISION_MATRIX = [
    # (id, cfg覆盖, delta_val, delta_train, view, wall秒, 期望accept, 期望失败闸门)
    ("clear_improvement_accept", {}, _delta(("v1", False, True, 0.4, 1.0),
                                            ("v2", True, True, 1.0, 1.0)), IMPROVED_TRAIN, CHEAP, 10.0, True, None),
    ("score_only_gain_accept", {
        "min_val_pass_rate_improvement": 0.0
    }, _delta(("v1", False, False, 0.4, 0.8), ("v2", True, True, 1.0, 1.0)), FLAT_TRAIN, CHEAP, 10.0, True, None),
    ("no_change_reject", {}, _delta(
        ("v1", False, False, 0.4, 0.4),
        ("v2", True, True, 1.0, 1.0)), FLAT_TRAIN, CHEAP, 10.0, False, "min_val_improvement"),
    ("val_regression_reject", {
        "overfit_guard": False
    }, _delta(("v1", True, False, 1.0, 0.2),
              ("v2", True, True, 1.0, 1.0)), FLAT_TRAIN, CHEAP, 10.0, False, "min_val_improvement"),
    ("overfit_reject", {}, _delta(("v1", True, False, 1.0, 0.2),
                                  ("v2", False, False, 0.3, 0.3)), IMPROVED_TRAIN, CHEAP, 10.0, False, "overfit_guard"),
    ("new_hard_fail_despite_net_gain_reject", {},
     _delta(("v1", False, True, 0.2, 1.0), ("v2", False, True, 0.2, 1.0),
            ("v3", True, False, 1.0, 0.4)), IMPROVED_TRAIN, CHEAP, 10.0, False, "no_new_hard_fail"),
    ("protected_case_new_fail_reject", {
        "protected_cases": ["v_key"],
        "forbid_new_hard_fail": False
    }, _delta(("v1", False, True, 0.2, 1.0),
              ("v_key", True, False, 1.0, 0.4)), IMPROVED_TRAIN, CHEAP, 10.0, False, "protected_cases"),
    ("protected_case_score_down_reject", {
        "protected_cases": ["v_key"]
    }, _delta(("v1", False, True, 0.2, 1.0),
              ("v_key", True, True, 1.0, 0.9)), IMPROVED_TRAIN, CHEAP, 10.0, False, "protected_cases"),
    ("cost_over_budget_reject", {
        "max_cost_usd": 0.5
    }, _delta(("v1", False, True, 0.2, 1.0)), IMPROVED_TRAIN, {
        "total_llm_cost": 0.75,
        "budget_used": 10,
        "duration_seconds": 5.0
    }, 10.0, False, "cost_budget"),
    ("metric_calls_over_budget_reject", {
        "max_metric_calls": 50
    }, _delta(("v1", False, True, 0.2, 1.0)), IMPROVED_TRAIN, {
        "total_llm_cost": 0.01,
        "budget_used": 61,
        "duration_seconds": 5.0
    }, 10.0, False, "cost_budget"),
    ("metric_calls_budget_untracked_reject", {
        "max_metric_calls": 50
    }, _delta(("v1", False, True, 0.2, 1.0)), IMPROVED_TRAIN, {
        "total_llm_cost": 0.01,
        "budget_used": None,
        "duration_seconds": 5.0
    }, 10.0, False, "cost_budget"),  # fail-closed：配置了预算却无追踪数据 → 拒绝
    ("duration_over_budget_reject", {
        "max_duration_seconds": 30.0
    }, _delta(("v1", False, True, 0.2, 1.0)), IMPROVED_TRAIN, CHEAP, 45.0, False, "duration_budget"),
    ("non_protected_new_fail_allowed_when_disabled", {
        "forbid_new_hard_fail": False,
        "protected_cases": []
    }, _delta(("v1", False, True, 0.2, 1.0), ("v2", False, True, 0.2, 1.0),
              ("v3", True, False, 1.0, 0.4)), IMPROVED_TRAIN, CHEAP, 10.0, True, None),
]


@pytest.mark.parametrize(
    "case_id,cfg_overrides,delta_val,delta_train,view,wall,expect_accept,expect_failed_gate",
    DECISION_MATRIX,
    ids=[row[0] for row in DECISION_MATRIX],
)
def test_gate_decision_matrix(case_id, cfg_overrides, delta_val, delta_train, view, wall, expect_accept,
                              expect_failed_gate):
    cfg = GateConfig(**cfg_overrides)
    decision = evaluate_gates(cfg,
                              delta_val=delta_val,
                              delta_train=delta_train,
                              optimize_result_view=view,
                              wall_seconds=wall)
    assert decision.accepted is expect_accept, f"{case_id}: {decision.reason}"
    assert len(decision.gates) == 6
    if expect_failed_gate is not None:
        failed_names = {g.name for g in decision.gates if not g.passed}
        assert expect_failed_gate in failed_names
        assert not decision.accepted
    assert decision.reason  # 决策必须带中文理由


def test_overfit_reason_mentions_overfitting():
    """过拟合场景的拒绝理由必须点名「过拟合」（即使同时触发别的闸门）。"""
    decision = evaluate_gates(
        GateConfig(protected_cases=["v1"]),
        delta_val=_delta(("v1", True, False, 1.0, 0.2), ("v2", False, False, 0.3, 0.3)),
        delta_train=IMPROVED_TRAIN,
        optimize_result_view=CHEAP,
        wall_seconds=10.0,
    )
    assert not decision.accepted
    assert "过拟合" in decision.reason
    # 同时触发的其它闸门也在理由里提示
    assert "同时未通过" in decision.reason


def test_cost_budget_fails_closed_when_budget_untracked():
    """配置了 max_metric_calls 但 budget_used 缺失 → fail-closed 拒绝并写明原因。"""
    view = {"total_llm_cost": 0.01, "budget_used": None, "duration_seconds": 5.0}
    decision = evaluate_gates(GateConfig(max_metric_calls=50),
                              delta_val=_delta(("v1", False, True, 0.2, 1.0)),
                              delta_train=IMPROVED_TRAIN,
                              optimize_result_view=view,
                              wall_seconds=1.0)
    gate = next(g for g in decision.gates if g.name == "cost_budget")
    assert gate.passed is False and decision.accepted is False
    assert "预算追踪不可用" in gate.detail
    # 未配置 max_metric_calls 时，budget_used 缺失不影响通过
    decision2 = evaluate_gates(GateConfig(),
                               delta_val=_delta(("v1", False, True, 0.2, 1.0)),
                               delta_train=IMPROVED_TRAIN,
                               optimize_result_view=view,
                               wall_seconds=1.0)
    assert next(g for g in decision2.gates if g.name == "cost_budget").passed is True


def test_config_rejects_typo_gate_keys(tmp_path):
    """闸门配置 fail-fast：写错闸门名必须报错，而不是静默用默认阈值。"""
    with pytest.raises(ValidationError):
        GateConfig(min_val_pass_rate_improvment=0.5)  # 拼写错误的字段名
    bad = tmp_path / "pipeline.bad.json"
    bad.write_text('{"gates": {"min_val_pass_rate_improvment": 0.5}}', encoding="utf-8")
    with pytest.raises(ValidationError):
        PipelineConfig.load(bad)
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate({"unknown_top_level": 1})


def test_accept_reason_is_positive():
    decision = evaluate_gates(
        GateConfig(),
        delta_val=_delta(("v1", False, True, 0.2, 1.0)),
        delta_train=FLAT_TRAIN,
        optimize_result_view=CHEAP,
        wall_seconds=1.0,
    )
    assert decision.accepted
    assert "全部闸门通过" in decision.reason
    assert all(g.passed for g in decision.gates)
