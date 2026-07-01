# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""优化阶段：产出一个候选 prompt 组（三字段），供后续验证与门控。

两种后端
--------
- real：调**真实** ``AgentOptimizer.optimize``（GEPA 反思），
  ``update_source=False`` 让源文件在优化结束后自动还原到 baseline，
  最优候选从 ``OptimizeResult.best_prompts`` 取回。成本/耗时来自真实运行。
- fake：脚本化候选。刻意构造一个"训练集提升、验证集退化"的过拟合候选
  （给 skill 增加乘法能力，同时植入 ``assume-mul-default`` 过拟合副作用），
  用来离线、确定性地演示门控拒绝过拟合（验收第 3 条）。

两档统一输出 :class:`CandidateResult`（候选文本字典 + 成本 + 耗时 + 元信息）。
候选的"应用/还原"由 :func:`apply_candidate` / :func:`restore_prompts` 完成——
验证阶段把候选临时写入源 prompt 文件，评测后还原，real / fake 共用同一机制。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from agent import PROMPT_PATHS


CallAgent = Callable[[str], Awaitable[str]]


@dataclass
class CandidateResult:
    prompts: dict[str, str]           # field name -> 候选 prompt 全文
    status: str = "SUCCEEDED"
    stop_reason: str = ""
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    rounds: int = 0
    optimized_fields: list[str] = field(default_factory=list)
    rounds_detail: list[dict] = field(default_factory=list)  # 每轮候选 prompt + 分数 + 接受与否
    meta: dict = field(default_factory=dict)


def read_baseline_prompts() -> dict[str, str]:
    """读当前源 prompt 文件（baseline）文本，keyed by TargetPrompt 字段名。"""
    return {name: Path(path).read_text(encoding="utf-8") for name, path in PROMPT_PATHS.items()}


def apply_candidate(candidate: dict[str, str]) -> dict[str, str]:
    """把候选文本写入源 prompt 文件，返回原始快照供还原。"""
    snapshot = read_baseline_prompts()
    for name, text in candidate.items():
        if name in PROMPT_PATHS:
            Path(PROMPT_PATHS[name]).write_text(text, encoding="utf-8")
    return snapshot


def restore_prompts(snapshot: dict[str, str]) -> None:
    """还原源 prompt 文件到给定快照。"""
    for name, text in snapshot.items():
        if name in PROMPT_PATHS:
            Path(PROMPT_PATHS[name]).write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# fake 后端：脚本化过拟合候选
# --------------------------------------------------------------------------
_SKILL_CANDIDATE = """# 解题技能 · Skill Prompt（候选）

这里描述你会做哪些类型的算术题。只有列出的题型你才能解答；遇到未掌握的题型，
如实回答「抱歉，我暂时无法解答这道题。」，不要瞎猜。

## 已掌握题型
- 加法：把题目中的两个数量相加。
- 乘法：把题目中的两个数量相乘（新增）。

## 快捷启发式（反思器新增，实为过拟合）
- 数字较大的题目倾向于按乘法处理，往往更快得到答案。

<!-- @cap: op-add -->
<!-- @cap: op-mul -->
<!-- @cap: assume-mul-default -->
"""


async def optimize_fake(train_path: Path, val_path: Path) -> CandidateResult:
    """确定性脚本候选：加乘法能力（真实提升）+ 过拟合副作用（制造验证退化）。"""
    start = time.time()
    baseline = read_baseline_prompts()
    candidate = dict(baseline)
    candidate["skill"] = _SKILL_CANDIDATE  # 只改 skill 字段；router/system 保持不变
    rounds_detail = [{
        "round": 1,
        "optimized_fields": ["skill"],
        "candidate_prompts": candidate,
        "note": "脚本化过拟合候选：新增乘法能力 + assume-mul-default 副作用",
    }]
    return CandidateResult(
        prompts=candidate,
        status="SUCCEEDED",
        stop_reason="fake_scripted_candidate",
        cost_usd=0.0,
        duration_seconds=round(time.time() - start, 4),
        rounds=1,
        optimized_fields=["skill"],
        rounds_detail=rounds_detail,
        meta={"backend": "fake", "note": "scripted overfitting candidate for offline demo"},
    )


# --------------------------------------------------------------------------
# real 后端：真实 AgentOptimizer(GEPA)
# --------------------------------------------------------------------------
async def optimize_real(
    config_path: Path,
    call_agent: CallAgent,
    train_path: Path,
    val_path: Path,
    output_dir: Path,
) -> CandidateResult:
    """调真实 AgentOptimizer；update_source=False 让源文件优化后自动还原。"""
    from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

    target = TargetPrompt()
    for name, path in PROMPT_PATHS.items():
        target.add_path(name, str(path))

    result = await AgentOptimizer.optimize(
        config_path=str(config_path),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(output_dir),
        update_source=False,  # 候选先不落源，交由 pipeline 门控决定是否接受
        verbose=1,
    )
    return CandidateResult(
        prompts=dict(result.best_prompts),
        status=str(result.status),
        stop_reason=str(result.stop_reason or ""),
        cost_usd=float(result.total_llm_cost),
        duration_seconds=float(result.duration_seconds),
        rounds=len(result.rounds),
        optimized_fields=sorted(
            {f for r in result.rounds for f in r.optimized_field_names}
        ),
        rounds_detail=[
            {
                "round": r.round,
                "optimized_fields": list(r.optimized_field_names),
                "candidate_prompts": dict(r.candidate_prompts),
                "validation_pass_rate": r.validation_pass_rate,
                "metric_breakdown": dict(r.metric_breakdown),
                "accepted": r.accepted,
                "acceptance_reason": r.acceptance_reason,
                "cost_usd": r.round_llm_cost,
                "duration_seconds": r.duration_seconds,
            }
            for r in result.rounds
        ],
        meta={
            "backend": "real",
            "algorithm": result.algorithm,
            "baseline_pass_rate": result.baseline_pass_rate,
            "best_pass_rate": result.best_pass_rate,
        },
    )
