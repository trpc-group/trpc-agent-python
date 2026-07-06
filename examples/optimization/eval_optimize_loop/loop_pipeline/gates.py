# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""阶段⑤：可配置接受策略 —— 六道闸门，全过才接受。

===================== =========================================================
闸门                    规则（对应 GateConfig 字段）
===================== =========================================================
min_val_improvement    验证集通过率提升 ≥ min_val_pass_rate_improvement 且
                       平均分提升 ≥ min_val_score_improvement（双信号）
no_new_hard_fail       不允许任何 case 从 pass 变 fail（forbid_new_hard_fail）
protected_cases        保护 case 出现 new_fail / score_down 即拒绝
overfit_guard          训练集通过率↑ 且 验证集通过率↓ → 判定过拟合，拒绝
cost_budget            优化成本 ≤ max_cost_usd；若配置 max_metric_calls，
                       优化器 metric 调用数也不得超出（缺少 budget_used
                       追踪数据时 fail-closed，按未通过处理）
duration_budget        pipeline 墙钟时长 ≤ max_duration_seconds
===================== =========================================================

决策 = 所有闸门 AND；``reason`` 按**严重度**取最关键的失败闸门的中文说明
（过拟合 > 保护 case 退化 > 新增 hard fail > 提升不足 > 预算类），并注明
共有几道闸门未通过 —— overfit 场景往往同时触发多门，报告应点出根因而不是
恰好排在最前面的那一门。``optimize_result_view`` 用普通 dict 传入
（total_llm_cost / budget_used / duration_seconds），单测可以直接喂合成值
而不必构造完整的 OptimizeResult。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import GateConfig
from .regression import DeltaSummary


@dataclass
class GateResult:
    """单道闸门的判定。"""

    name: str
    passed: bool
    detail: str  # 中文说明（含实际值 vs 阈值）


@dataclass
class GateDecision:
    """最终接受/拒绝决策。"""

    accepted: bool
    reason: str
    gates: list[GateResult] = field(default_factory=list)


# reason 的严重度排序：越靠前越接近「候选本质有问题」，预算类殿后
_REASON_SEVERITY = (
    "overfit_guard",
    "protected_cases",
    "no_new_hard_fail",
    "min_val_improvement",
    "cost_budget",
    "duration_budget",
)


def _protected_violations(cfg: GateConfig, delta_val: DeltaSummary) -> list[str]:
    violations = []
    protected = set(cfg.protected_cases)
    for case in delta_val.per_case:
        if case.eval_id in protected and case.change in ("new_fail", "score_down"):
            violations.append(f"{case.eval_id}({case.change})")
    return violations


def evaluate_gates(
    cfg: GateConfig,
    *,
    delta_val: DeltaSummary,
    delta_train: DeltaSummary,
    optimize_result_view: dict[str, Any],
    wall_seconds: float,
) -> GateDecision:
    """按 GateConfig 逐门判定，返回整体决策与逐门明细。"""
    gates: list[GateResult] = []

    # 1. 验证集最小提升（通过率 + 平均分双信号）
    pass_ok = delta_val.pass_rate_delta >= cfg.min_val_pass_rate_improvement
    score_ok = delta_val.score_delta >= cfg.min_val_score_improvement
    gates.append(
        GateResult(
            name="min_val_improvement",
            passed=pass_ok and score_ok,
            detail=(f"验证集通过率提升 {delta_val.pass_rate_delta:+.4f}"
                    f"（要求 ≥ {cfg.min_val_pass_rate_improvement:g}），"
                    f"平均分提升 {delta_val.score_delta:+.4f}"
                    f"（要求 ≥ {cfg.min_val_score_improvement:g}）" + ("" if pass_ok and score_ok else " —— 提升不足，不值得接受")),
        ))

    # 2. 不允许新增 hard fail
    new_fails = [c.eval_id for c in delta_val.per_case if c.change == "new_fail"]
    new_fail_ok = (not cfg.forbid_new_hard_fail) or not new_fails
    if not new_fails:
        new_fail_detail = "验证集无新增失败 case"
    else:
        new_fail_detail = f"验证集新增失败 case：{'、'.join(new_fails)}"
        if not new_fail_ok:
            new_fail_detail += " —— 禁止新增 hard fail"
    gates.append(GateResult(name="no_new_hard_fail", passed=new_fail_ok, detail=new_fail_detail))

    # 3. 保护 case 不能退化
    violations = _protected_violations(cfg, delta_val)
    gates.append(
        GateResult(
            name="protected_cases",
            passed=not violations,
            detail=(f"保护 case（{'、'.join(cfg.protected_cases) or '无'}）均未退化"
                    if not violations else f"保护 case 退化：{'、'.join(violations)}"),
        ))

    # 4. 过拟合守卫
    overfit = (cfg.overfit_guard and delta_train.pass_rate_delta > 0 and delta_val.pass_rate_delta < 0)
    gates.append(
        GateResult(
            name="overfit_guard",
            passed=not overfit,
            detail=(f"训练集通过率提升 {delta_train.pass_rate_delta:+.4f} 且验证集退化 "
                    f"{delta_val.pass_rate_delta:+.4f}，判定过拟合，必须拒绝"
                    if overfit else f"未触发过拟合守卫（train {delta_train.pass_rate_delta:+.4f} / "
                    f"val {delta_val.pass_rate_delta:+.4f}）"),
        ))

    # 5. 成本预算（配置了 max_metric_calls 但拿不到 budget_used 时 fail-closed）
    total_cost = float(optimize_result_view.get("total_llm_cost") or 0.0)
    budget_used = optimize_result_view.get("budget_used")
    cost_ok = total_cost <= cfg.max_cost_usd
    budget_untracked = cfg.max_metric_calls is not None and budget_used is None
    calls_ok = (cfg.max_metric_calls is None or (budget_used is not None and int(budget_used) <= cfg.max_metric_calls))
    calls_part = ""
    if cfg.max_metric_calls is not None:
        calls_part = (f"；metric 调用 {budget_used if budget_used is not None else '未知'}"
                      f"（预算 {cfg.max_metric_calls}）")
    cost_detail = f"优化成本 ${total_cost:.4f}（预算 ${cfg.max_cost_usd:g}）{calls_part}"
    if budget_untracked:
        cost_detail += " —— 预算追踪不可用：已配置 max_metric_calls 但无 budget_used 数据，按未通过处理"
    elif not (cost_ok and calls_ok):
        cost_detail += " —— 超出成本预算"
    gates.append(GateResult(name="cost_budget", passed=cost_ok and calls_ok, detail=cost_detail))

    # 6. 时长预算
    duration_ok = wall_seconds <= cfg.max_duration_seconds
    duration_detail = f"pipeline 耗时 {wall_seconds:.1f}s（预算 {cfg.max_duration_seconds:g}s）"
    if not duration_ok:
        duration_detail += " —— 超出时长预算"
    gates.append(GateResult(name="duration_budget", passed=duration_ok, detail=duration_detail))

    accepted = all(g.passed for g in gates)
    if accepted:
        reason = "全部闸门通过：验证集有实际提升、无退化、成本与耗时均在预算内，候选值得接受。"
    else:
        failed = {g.name: g for g in gates if not g.passed}
        key_gate = next(failed[name] for name in _REASON_SEVERITY if name in failed)
        reason = f"闸门 {key_gate.name} 未通过：{key_gate.detail}"
        if len(failed) > 1:
            others = "、".join(name for name in failed if name != key_gate.name)
            reason += f"（另有 {len(failed) - 1} 道闸门同时未通过：{others}）"
    return GateDecision(accepted=accepted, reason=reason, gates=gates)
