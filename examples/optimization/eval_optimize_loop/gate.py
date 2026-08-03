# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Acceptance gate: 可配置的候选 prompt 接受/拒绝策略。

防过拟合的核心在这里：即使验证集总分提升，只要出现"关键 case 退化"或
"新增 hard fail"，候选一律拒绝。成本/耗时预算作为硬上限兜底。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Optional


@dataclass
class GateConfig:
    """接受策略配置（来自 optimizer.json 的 optimize.gate）。"""
    min_val_improvement: float = 0.0
    no_new_hard_fail: bool = True
    key_cases_no_regression: bool = True
    max_cost_usd: float = 0.0
    key_cases: list[str] = field(default_factory=list)


@dataclass
class CaseDelta:
    """逐 case 对比（baseline vs candidate）。"""
    eval_id: str
    baseline_pass: bool
    candidate_pass: bool
    delta: str  # "new_pass" | "new_fail" | "kept_pass" | "kept_fail" | "score_up" | "score_down"


@dataclass
class GateDecision:
    accept: bool
    reasons: list[str]
    val_score_before: float
    val_score_after: float
    improvement: float
    case_deltas: list[CaseDelta]


def _pass_rate(pass_map: dict) -> float:
    if not pass_map:
        return 0.0
    return sum(1 for v in pass_map.values() if v) / len(pass_map)


def evaluate_gate(
    base_pass: dict[str, bool],
    cand_pass: dict[str, bool],
    gate_cfg: GateConfig,
    cost_usd: float = 0.0,
    duration_seconds: float = 0.0,
) -> GateDecision:
    """对单个候选做出接受/拒绝决策。

    Args:
        base_pass: dict[eval_id, baseline 是否通过]
        cand_pass: dict[eval_id, candidate 是否通过]
        gate_cfg: 接受策略
        cost_usd / duration_seconds: 运行成本（fake 模式通常为 0）
    """
    reasons: list[str] = []
    case_deltas: list[CaseDelta] = []

    before = _pass_rate(base_pass)
    after = _pass_rate(cand_pass)
    improvement = after - before

    for eval_id in sorted(set(base_pass) | set(cand_pass)):
        b = base_pass.get(eval_id, False)
        c = cand_pass.get(eval_id, False)
        if (not b) and c:
            delta = "new_pass"
        elif b and (not c):
            delta = "new_fail"
        elif b and c:
            delta = "kept_pass"
        else:
            delta = "kept_fail"
        case_deltas.append(CaseDelta(eval_id=eval_id, baseline_pass=b,
                                     candidate_pass=c, delta=delta))

    accept = True

    # 规则 1：验证集总分必须提升 >= 阈值
    if improvement < gate_cfg.min_val_improvement:
        accept = False
        reasons.append(
            f"验证集总分未达提升阈值：{before:.4f} -> {after:.4f} "
            f"(需提升 >= {gate_cfg.min_val_improvement:.4f})"
        )

    # 规则 2：禁止新增 hard fail（baseline 通过的 case 在候选下失败）
    if gate_cfg.no_new_hard_fail:
        new_fails = [d.eval_id for d in case_deltas if d.delta == "new_fail"]
        if new_fails:
            accept = False
            reasons.append(f"出现新增 hard fail（baseline 通过但候选失败）：{new_fails}")

    # 规则 3：关键 case 不能退化
    if gate_cfg.key_cases_no_regression:
        regressed_keys = [
            d.eval_id for d in case_deltas
            if d.delta == "new_fail" and d.eval_id in gate_cfg.key_cases
        ]
        if regressed_keys:
            accept = False
            reasons.append(f"关键 case 退化（过拟合信号）：{regressed_keys}")

    # 规则 4：成本/预算硬上限
    if gate_cfg.max_cost_usd > 0 and cost_usd > gate_cfg.max_cost_usd:
        accept = False
        reasons.append(f"运行成本 {cost_usd:.4f} USD 超过预算 {gate_cfg.max_cost_usd:.4f} USD")

    if accept:
        reasons.append(
            f"验证集总分提升 {before:.4f} -> {after:.4f} (+{improvement:+.4f})，"
            f"无新增 hard fail、关键 case 未退化、成本在预算内。"
        )

    return GateDecision(
        accept=accept, reasons=reasons, val_score_before=before,
        val_score_after=after, improvement=improvement, case_deltas=case_deltas,
    )
