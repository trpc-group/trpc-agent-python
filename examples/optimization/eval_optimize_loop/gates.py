#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Gate：纯规则决策，输入两个 split 的 case-level delta，输出 ACCEPT/REJECT。

对应 Issue #91 的 G1-G7 接受策略：

  G1  validation 最小有效提升         val_delta >= min_val_lift
  G2  train 涨 val 跌 = 过拟合        not (train_delta > min_val_lift and val_delta < -min_val_lift)
  G3  不新增 hard fail                risk_level==high 的 case 不能从 PASSED→FAILED
  G4  protected case 不退化           protected==true 的 case 不能分数下降
  G5  slice 平均退化不超过 tolerance  每个 slice 平均 delta >= -slice_tol
  G6  成本证据完整                    cost_status != "unavailable" and total_tokens <= budget
  G7  明显微小变化不算有效提升         候选接受时，关键 delta 必须 > epsilon

不写互相重复的规则：G1 看 val 整体；G5 看 slice；G3/G4 看个体风险；G6 看成本；G7 防 epsilon 内噪声。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class CaseDelta:
    """单 case 的对比信息。

    Attributes:
        eval_id:           case id
        split:             train | val
        slice_name:        来自 state.slice
        risk_level:        low | mid | high
        protected:         bool
        champion_status:   PASSED | FAILED
        challenger_status: PASSED | FAILED
        champion_score:    0~1
        challenger_score:  0~1
        delta:             challenger - champion
    """

    eval_id: str
    split: str
    slice_name: str
    risk_level: str = "low"
    protected: bool = False
    champion_status: str = "FAILED"
    challenger_status: str = "FAILED"
    champion_score: float = 0.0
    challenger_score: float = 0.0

    @property
    def delta(self) -> float:
        return self.challenger_score - self.champion_score

    @property
    def new_hard_fail(self) -> bool:
        """risk_level==high 且 champion 通过但 challenger 失败。"""
        return self.risk_level == "high" and self.champion_status == "PASSED" and self.challenger_status != "PASSED"


@dataclass
class GateConfig:
    """Gate 阈值。所有字段必须可从 run.json 反序列化。"""

    min_val_lift: float = 0.02
    slice_tolerance: float = 0.05
    budget_tokens: int = 100_000
    budget_usd: Optional[float] = None
    epsilon: float = 0.001


@dataclass
class Decision:
    accepted: bool
    violated: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)


def _avg(values: Iterable[float]) -> float:
    vs = list(values)
    return sum(vs) / len(vs) if vs else 0.0


def _slice_avg_deltas(cases: List[CaseDelta]):
    """按 slice 聚合 delta，返回 {slice_name: avg_delta}。"""
    groups: dict[str, list[float]] = {}
    for c in cases:
        groups.setdefault(c.slice_name, []).append(c.delta)
    return {k: _avg(v) for k, v in groups.items()}


def evaluate(
    cases: List[CaseDelta],
    *,
    cost_status: str = "measured",
    total_tokens: Optional[int] = 0,
    total_cost: Optional[float] = 0.0,
    config: Optional[GateConfig] = None,
) -> Decision:
    """主入口。

    Args:
        cases:        train + val 两个 split 全部 case 的 delta（合并传入）
        cost_status:  measured / unavailable
        total_tokens: 实际消耗 token；fake/trace 模式为 0
        config:       gate 阈值，None 用默认
    """
    cfg = config or GateConfig()
    violated: List[str] = []
    reasons: List[str] = []

    train_cases = [c for c in cases if c.split == "train"]
    val_cases = [c for c in cases if c.split == "val"]
    train_delta = _avg(c.delta for c in train_cases)
    val_delta = _avg(c.delta for c in val_cases)

    # G2 必须先于 G1：先识别"训练涨 / val 跌"的过拟合，再判 val 是否涨够。
    # G2: overfit
    if train_delta > cfg.min_val_lift and val_delta < -cfg.min_val_lift:
        violated.append("G2")
        reasons.append(
            f"过拟合：train_delta={train_delta:+.4f} 上升而 " f"val_delta={val_delta:+.4f} 下降，候选只记住训练集。"
        )

    # G1: val minimal lift
    if val_delta < cfg.min_val_lift:
        violated.append("G1")
        reasons.append(f"验证集无有效提升：val_delta={val_delta:+.4f} < " f"min_val_lift={cfg.min_val_lift:.4f}。")

    # G3: no new hard fail
    new_hard = [c.eval_id for c in cases if c.new_hard_fail]
    if new_hard:
        violated.append("G3")
        reasons.append(f"新增 hard fail（risk_level==high 由 PASSED→FAILED）：{new_hard}。")

    # G4: protected case no regression
    protected_reg = [c.eval_id for c in cases if c.protected and c.delta < -cfg.epsilon]
    if protected_reg:
        violated.append("G4")
        reasons.append(f"protected case 退化：{protected_reg}。")

    # G5: slice tolerance
    slice_deltas = _slice_avg_deltas(cases)
    bad_slices = {k: v for k, v in slice_deltas.items() if v < -cfg.slice_tolerance}
    if bad_slices:
        violated.append("G5")
        reasons.append(
            f"slice 退化超过 tolerance={cfg.slice_tolerance:.4f}："
            f"{ {k: round(v, 4) for k, v in bad_slices.items()} }。"
        )

    # G6: cost evidence
    if cost_status == "unavailable":
        violated.append("G6")
        reasons.append("成本证据缺失（cost_status=unavailable），不可自动 ACCEPT。")
    elif total_tokens is None or total_cost is None:
        violated.append("G6")
        reasons.append("成本状态标为 measured，但 token/cost 证据为空，不可自动 ACCEPT。")
    elif total_tokens > cfg.budget_tokens:
        violated.append("G6")
        reasons.append(f"超成本预算：total_tokens={total_tokens} > " f"budget_tokens={cfg.budget_tokens}。")
    elif cfg.budget_usd is not None and total_cost > cfg.budget_usd:
        violated.append("G6")
        reasons.append(f"超成本预算：total_cost={total_cost:.6f} > " f"budget_usd={cfg.budget_usd:.6f}。")

    # G7: epsilon guard —— 候选接受时，val_delta 必须严格 > epsilon（非噪声）
    # 仅在前面 6 条全过时才检查；它防的是 "val_delta==0.0001" 这种几乎为零的波动
    # 被 G1 当作有效提升（因为 G1 用 >= min_val_lift 默认 0.02 已经隐含保护，
    # 但当用户把 min_val_lift 调到 0 时 G7 仍是最后一道闸）。
    if not violated and val_delta <= cfg.epsilon:
        violated.append("G7")
        reasons.append(f"val_delta={val_delta:+.4f} ≤ epsilon={cfg.epsilon:.4f}，" "属于明显微小变化，不视为有效提升。")

    accepted = not violated
    return Decision(accepted=accepted, violated=violated, reasons=reasons)
