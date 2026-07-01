# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""门控阶段：逐 case delta + 可配置接受策略（含防过拟合）。

先把 baseline 验证集与候选验证集逐 case 对比，分出
新增通过 / 新增失败 / 分数提升 / 分数下降 / 不变；再按 config 里的 gate
规则综合裁决 accept / reject，并给出逐条规则的通过情况与理由。

防过拟合的关键在 ``forbid_new_hard_fail`` 与 ``key_case_ids``：只要验证集里
出现"原本通过、优化后失败"的退化，即便训练集提升、验证集总分持平，也会拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evaluate import SetEval


@dataclass
class CaseDelta:
    eval_id: str
    baseline_passed: bool
    candidate_passed: bool
    baseline_score: float
    candidate_score: float
    status: str  # newly_passed | newly_failed | improved | regressed | unchanged

    @property
    def score_delta(self) -> float:
        return round(self.candidate_score - self.baseline_score, 4)


def compute_delta(baseline: SetEval, candidate: SetEval) -> list[CaseDelta]:
    """逐 case 对比 baseline 与候选的验证结果。"""
    deltas: list[CaseDelta] = []
    for eval_id, base_case in baseline.cases.items():
        cand_case = candidate.cases.get(eval_id)
        if cand_case is None:
            continue
        bp, cp = base_case.passed, cand_case.passed
        bs, cs = base_case.score, cand_case.score
        if not bp and cp:
            status = "newly_passed"
        elif bp and not cp:
            status = "newly_failed"
        elif cs > bs:
            status = "improved"
        elif cs < bs:
            status = "regressed"
        else:
            status = "unchanged"
        deltas.append(CaseDelta(eval_id, bp, cp, bs, cs, status))
    return deltas


@dataclass
class RuleResult:
    name: str
    passed: bool
    detail: str


@dataclass
class GateDecision:
    accepted: bool
    rules: list[RuleResult] = field(default_factory=list)
    summary: str = ""
    val_score_delta: float = 0.0
    newly_passed: list[str] = field(default_factory=list)
    newly_failed: list[str] = field(default_factory=list)
    regressed: list[str] = field(default_factory=list)


def evaluate_gate(
    baseline_val: SetEval,
    candidate_val: SetEval,
    deltas: list[CaseDelta],
    candidate_cost: float,
    gate_config: dict,
) -> GateDecision:
    """按 gate 配置综合裁决。任一 rule 不过即拒绝。"""
    min_delta = float(gate_config.get("min_val_score_delta", 0.0))
    forbid_new_hard_fail = bool(gate_config.get("forbid_new_hard_fail", True))
    key_case_ids = set(gate_config.get("key_case_ids", []))
    cost_budget = float(gate_config.get("cost_budget_usd", float("inf")))

    val_delta = round(candidate_val.avg_score - baseline_val.avg_score, 4)
    newly_failed = [d.eval_id for d in deltas if d.status == "newly_failed"]
    newly_passed = [d.eval_id for d in deltas if d.status == "newly_passed"]
    regressed = [d.eval_id for d in deltas if d.status in ("newly_failed", "regressed")]

    rules: list[RuleResult] = []

    # R1: 验证集总分提升 >= 阈值
    rules.append(RuleResult(
        "min_val_score_delta",
        val_delta >= min_delta,
        f"验证集平均分 delta={val_delta:+.4f}，阈值 ≥ {min_delta:+.4f}",
    ))

    # R2: 不得新增 hard fail（原通过 → 现失败）——防过拟合主闸
    rules.append(RuleResult(
        "forbid_new_hard_fail",
        (not forbid_new_hard_fail) or (len(newly_failed) == 0),
        f"新增失败 case={newly_failed or '无'}",
    ))

    # R3: 关键 case 不得退化
    key_regressed = [d.eval_id for d in deltas
                     if d.eval_id in key_case_ids and d.status in ("newly_failed", "regressed")]
    rules.append(RuleResult(
        "key_cases_no_regression",
        len(key_regressed) == 0,
        f"关键 case={sorted(key_case_ids) or '未指定'}，其中退化={key_regressed or '无'}",
    ))

    # R4: 成本预算
    rules.append(RuleResult(
        "cost_within_budget",
        candidate_cost <= cost_budget,
        f"候选成本=${candidate_cost:.4f}，预算 ≤ ${cost_budget:.4f}",
    ))

    accepted = all(r.passed for r in rules)
    failed_rules = [r.name for r in rules if not r.passed]
    if accepted:
        summary = f"接受候选：验证集提升 {val_delta:+.4f} 且未触发任何拒绝规则。"
    else:
        summary = (
            f"拒绝候选：命中规则 {failed_rules}。"
            + ("疑似过拟合（验证集出现退化/新增失败）。" if newly_failed else "")
        )

    return GateDecision(
        accepted=accepted,
        rules=rules,
        summary=summary,
        val_score_delta=val_delta,
        newly_passed=newly_passed,
        newly_failed=newly_failed,
        regressed=regressed,
    )
