"""决策准确率测试 — 验收标准 #2 的自有隐藏样本验证。

官方隐藏集未公开，这里用一组（配置, 期望决策）的黄金样本锁定决策行为：
对可优化成功/优化无效/过拟合退化等多类变体，gate 的接受/拒绝/需审查决策
必须与期望一致，正确率 ≥ 80%。
"""

import sys
from pathlib import Path

import pytest

_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.attribution import attribute_failures
from pipeline.baseline import run_baseline_fake
from pipeline.config import load_pipeline_config
from pipeline.gate import GateDecision, evaluate_gate
from pipeline.optimize import run_optimize_fake
from pipeline.validate import run_validation_trace


def _run_pipeline_stages(cfg):
    """跑 baseline → attribution → optimize → validate → gate，返回 gate。"""
    baseline_train = run_baseline_fake(cfg.train_evalset, cfg)
    baseline_val = run_baseline_fake(cfg.val_evalset, cfg)
    attribution = attribute_failures(
        baseline_train.__dict__, baseline_val.__dict__,
    )
    optimize_result = run_optimize_fake(attribution, cfg, scenario=cfg.scenario)
    validation = run_validation_trace(
        cfg.train_evalset, cfg.val_evalset, baseline_val,
        optimize_result, cfg, scenario=cfg.scenario,
        val_regression_cases=cfg.val_regression_cases,
    )
    candidate_train = validation.candidate_train or baseline_train
    gate = evaluate_gate(
        baseline_pass_rate=baseline_train.pass_rate,
        candidate_pass_rate=candidate_train.pass_rate,
        baseline_metrics=baseline_train.metric_breakdown,
        candidate_metrics=candidate_train.metric_breakdown,
        min_improvement=cfg.min_improvement_threshold,
        max_cost=cfg.max_cost_budget,
        optimization_cost=optimize_result.total_cost,
        validation_new_failures=validation.new_failures,
    )
    return gate


@pytest.fixture
def data_dir():
    return _parent / "data"


def _cfg(data_dir, **overrides):
    base = dict(
        mode="fake",
        train_evalset=str(data_dir / "train.evalset.json"),
        val_evalset=str(data_dir / "val.evalset.json"),
    )
    base.update(overrides)
    return load_pipeline_config(**base)


# 黄金样本：(描述, 配置覆盖, 期望决策)
GOLD_CASES = [
    ("fix_attributed 默认", {"scenario": "fix_attributed"}, GateDecision.ACCEPT),
    ("fix_attributed 更多轮次", {"scenario": "fix_attributed", "max_iterations": 5}, GateDecision.ACCEPT),
    ("noop 无实质改进", {"scenario": "noop"}, GateDecision.NEEDS_REVIEW),
    ("noop 换种子", {"scenario": "noop", "seed": 7}, GateDecision.NEEDS_REVIEW),
    ("overfit val 回归", {"scenario": "overfit",
                          "val_regression_cases": ["val_simple_math_001", "val_reasoning_001"]},
     GateDecision.REJECT),
    ("overfit 换回归 case", {"scenario": "overfit",
                             "val_regression_cases": ["val_chinese_001"]},
     GateDecision.REJECT),
]


def test_decision_accuracy_ge_80_percent(data_dir):
    """隐藏样本上优化接受/拒绝决策准确率 ≥ 80%（自有黄金样本验证）。"""
    correct = 0
    failures = []
    for desc, overrides, expected in GOLD_CASES:
        cfg = _cfg(data_dir, **overrides)
        try:
            gate = _run_pipeline_stages(cfg)
        except Exception as e:  # noqa: BLE001 — 场景异常也算决策失败
            failures.append((desc, expected, f"异常: {e}"))
            continue
        if gate.decision == expected:
            correct += 1
        else:
            failures.append((desc, expected, gate.decision))
    total = len(GOLD_CASES)
    accuracy = correct / total
    assert accuracy >= 0.8, (
        f"决策准确率 {accuracy:.0%} < 80% （{correct}/{total}），失败: {failures}"
    )
