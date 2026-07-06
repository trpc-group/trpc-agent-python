# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""阶段③：AgentOptimizer 封装 —— 场景 → 优化配置 / 优化器验证集的映射。

三个演示场景只差两处输入（这正是「同一条 pipeline、不同数据/配置产生
不同决策」的演示点）：

=========== ============================== ==================================
场景         optimizer 配置                   优化器眼中的「验证集」
=========== ============================== ==================================
success     optimizer.json                  data/val.evalset.json（独立）
no_effect   configs/optimizer.no_effect.json data/val.evalset.json（独立）
overfit     configs/optimizer.overfit.json  data/optimizer_probe.evalset.json
                                            （与训练集同源的泄漏调参集！）
=========== ============================== ==================================

overfit 场景的要点：优化器视角里 probe 集分数一路变好（0/3 → 3/3），
它自己完全不知道过拟合了 —— 只有 pipeline 阶段④ 用**独立** val 集复评
才能揭穿。配置差异仅在 ``reflection_lm.model_name``（决定 fake 反思模型
返回哪套候选）。

``AgentOptimizer.optimize`` 自身会把每轮候选、接受理由、成本、耗时、
seed（config.snapshot.json）落盘到 ``output_dir`` —— 阶段⑥ 的审计产物
直接复用这套 SDK 原生审计目录。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trpc_agent_sdk.evaluation import AgentOptimizer, OptimizeResult, TargetPrompt

SCENARIOS = ("success", "no_effect", "overfit")


@dataclass(frozen=True)
class ScenarioSpec:
    """一个演示场景的输入组合。"""

    name: str
    optimizer_config: Path
    optimizer_val_dataset: Path
    train_dataset: Path


def resolve_scenario(name: str, example_root: Path) -> ScenarioSpec:
    """场景名 → 输入组合；未知场景抛 ValueError。"""
    root = example_root
    train = root / "data" / "train.evalset.json"
    if name == "success":
        return ScenarioSpec(name, root / "optimizer.json", root / "data" / "val.evalset.json", train)
    if name == "no_effect":
        return ScenarioSpec(name, root / "configs" / "optimizer.no_effect.json", root / "data" / "val.evalset.json",
                            train)
    if name == "overfit":
        return ScenarioSpec(name, root / "configs" / "optimizer.overfit.json",
                            root / "data" / "optimizer_probe.evalset.json", train)
    raise ValueError(f"未知场景 {name!r}；可选：{', '.join(SCENARIOS)}")


async def run_optimization(
    spec: ScenarioSpec,
    *,
    call_agent,
    target: TargetPrompt,
    output_dir: Path,
) -> OptimizeResult:
    """跑一轮 AgentOptimizer；SDK 审计产物落在 ``output_dir`` 下。

    ``update_source=False``：优化器结束后源 prompt 恢复 baseline；
    是否把最优候选写回源文件由 pipeline 的 gate 决策（``--apply``）决定，
    而不是优化器自作主张 —— 这是本闭环与"裸跑一次 AgentOptimizer"的
    核心区别。
    """
    return await AgentOptimizer.optimize(
        config_path=str(spec.optimizer_config),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(spec.train_dataset),
        validation_dataset_path=str(spec.optimizer_val_dataset),
        output_dir=str(output_dir),
        update_source=False,
        verbose=0,
    )
