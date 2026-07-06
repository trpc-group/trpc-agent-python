# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""阶段④：候选回归 —— 换入候选 prompt 复评，与 baseline 做逐 case 对比。

两个关键设计：

1. **换入/换出永不污染源文件**：``evaluate_candidate`` 先快照当前 prompt，
   ``TargetPrompt.write_all``（原子写 + 回滚）换入候选，``try/finally``
   保证评完必然还原 —— 即使评测中途抛异常。
2. **delta 口径**：状态变化优先（fail→pass = ``new_pass``，pass→fail =
   ``new_fail``），状态不变时按 case 平均分 ± epsilon 判 ``score_up`` /
   ``score_down`` / ``unchanged``。这四类正是 issue 需求 4 点名的对比维度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from trpc_agent_sdk.evaluation import TargetPrompt

from .evaluate import CaseEvalRecord, run_eval

ChangeKind = Literal["new_pass", "new_fail", "score_up", "score_down", "unchanged"]

CHANGE_KINDS: tuple[ChangeKind, ...] = ("new_pass", "new_fail", "score_up", "score_down", "unchanged")

CHANGE_LABELS_ZH: dict[str, str] = {
    "new_pass": "新增通过",
    "new_fail": "新增失败",
    "score_up": "分数提升",
    "score_down": "分数下降",
    "unchanged": "无变化",
}


@dataclass
class CaseDelta:
    """一条 case 的 baseline vs candidate 对比。"""

    eval_id: str
    baseline_passed: bool
    candidate_passed: bool
    baseline_score: float
    candidate_score: float
    change: ChangeKind


@dataclass
class DeltaSummary:
    """一个切分的逐 case delta 汇总。"""

    per_case: list[CaseDelta] = field(default_factory=list)
    pass_rate_delta: float = 0.0
    score_delta: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)


def classify(baseline: CaseEvalRecord, candidate: CaseEvalRecord, eps: float) -> CaseDelta:
    """单条 case 的 delta 分类（状态优先，分数其次）。"""
    if not baseline.passed and candidate.passed:
        change: ChangeKind = "new_pass"
    elif baseline.passed and not candidate.passed:
        change = "new_fail"
    elif candidate.case_score > baseline.case_score + eps:
        change = "score_up"
    elif candidate.case_score < baseline.case_score - eps:
        change = "score_down"
    else:
        change = "unchanged"
    return CaseDelta(
        eval_id=baseline.eval_id,
        baseline_passed=baseline.passed,
        candidate_passed=candidate.passed,
        baseline_score=baseline.case_score,
        candidate_score=candidate.case_score,
        change=change,
    )


def compute_delta(
    baseline: dict[str, CaseEvalRecord],
    candidate: dict[str, CaseEvalRecord],
    eps: float,
) -> DeltaSummary:
    """整个切分的 delta 汇总；两侧 case 集合应一致（同一数据集）。"""
    summary = DeltaSummary(counts={kind: 0 for kind in CHANGE_KINDS})
    for eval_id in sorted(baseline):
        if eval_id not in candidate:  # pragma: no cover - 同数据集不应发生
            continue
        delta = classify(baseline[eval_id], candidate[eval_id], eps)
        summary.per_case.append(delta)
        summary.counts[delta.change] += 1
    total = len(summary.per_case)
    if total:
        baseline_pass = sum(1 for d in summary.per_case if d.baseline_passed)
        candidate_pass = sum(1 for d in summary.per_case if d.candidate_passed)
        summary.pass_rate_delta = (candidate_pass - baseline_pass) / total
        summary.score_delta = sum(d.candidate_score - d.baseline_score for d in summary.per_case) / total
    return summary


async def evaluate_candidate(
    target: TargetPrompt,
    candidate_prompts: dict[str, str],
    datasets: dict[str, str],
    eval_config_path: str,
    *,
    agent_module: Optional[str] = "loop_agent",
) -> dict[str, dict[str, CaseEvalRecord]]:
    """换入候选 prompt → 逐数据集复评 → 无条件还原源 prompt。

    Args:
        target: 已注册全部 prompt 字段的 TargetPrompt。
        candidate_prompts: 候选 prompt 文本（键必须与 target 注册名一致）。
        datasets: 切分名 → evalset 路径（如 {"train": ..., "val": ...}）。
        eval_config_path: 验收 metric 套件（与 baseline 同一份，口径可比）。

    Returns:
        切分名 → (eval_id → CaseEvalRecord)。
    """
    snapshot = await target.read_all()
    await target.write_all(candidate_prompts)
    try:
        results: dict[str, dict[str, CaseEvalRecord]] = {}
        for split, dataset_path in datasets.items():
            results[split] = await run_eval(dataset_path, eval_config_path, agent_module=agent_module)
        return results
    finally:
        await target.write_all(snapshot)  # 永不污染源 prompt 文件
