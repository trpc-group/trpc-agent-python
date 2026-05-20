# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Multi-Agent Pipeline example 的优化器入口。

适用场景
--------
业务侧已编排好多 sub-agent 协作链路（router / 分支 worker / summarizer 等），
希望在不修改链路代码的前提下，对每个 sub-agent 的 prompt 进行联合优化。

这个文件做什么
--------------
1. 注册 4 个 prompt 文件作为 TargetPrompt 的 4 个独立字段
2. 定义 call_agent 把 query 透传给整条 pipeline 链路
3. 调 AgentOptimizer.optimize 跑 GEPA 多模块协同优化

怎么跑
------
1) 配 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
2) python examples/optimization/multi_agent_pipeline/run_optimization.py
3) 看 runs/<时间戳>/best_prompts/ 下 4 个 .md 文件

关键配置（详见 README §5）
--------------------------
- module_selector="round_robin"  : 每轮反思只改 1 个字段，便于归因
- use_merge=true                 : 累积单字段改进后主动融合（多字段才有意义）
- reflection_history_top_k=3      : 多字段轮换时给反思 LM 更长历史

接入自有链路时改哪里
--------------------
- pipeline/orchestrator.py 中的 invoke_pipeline 替换为业务真实链路调用
  （HTTP / gRPC / 内部编排框架等任意形态）
- TargetPrompt.add_path 调整为业务各 sub-agent 实际读取的 prompt 文件路径
- 若 prompt 在配置中心而非本地，把 add_path 替换为 add_callback
  （参考 remote_prompt_store/ example）
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

from pipeline.orchestrator import (
    FACT_AGENT_PROMPT_PATH,
    MATH_AGENT_PROMPT_PATH,
    ROUTER_PROMPT_PATH,
    SUMMARIZER_PROMPT_PATH,
    invoke_pipeline,
)


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
RUNS_DIR = _HERE / "runs"


async def call_agent(query: str) -> str:
    """框架回调：把 query 透传给整条 pipeline 链路，返回最终答复。"""
    return await invoke_pipeline(query)


async def main() -> None:
    """组装 4 字段 TargetPrompt + 调 AgentOptimizer.optimize。"""
    # 4 个 add_path 注册多字段优化目标。GEPA 把每个 key 视为独立 component，
    # module_selector="round_robin" 让每轮只改其中 1 个，便于归因。
    target = (
        TargetPrompt()
        .add_path("router", str(ROUTER_PROMPT_PATH))
        .add_path("fact_agent", str(FACT_AGENT_PROMPT_PATH))
        .add_path("math_agent", str(MATH_AGENT_PROMPT_PATH))
        .add_path("summarizer", str(SUMMARIZER_PROMPT_PATH))
    )

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = RUNS_DIR / timestamp

    await AgentOptimizer.optimize(
        config_path=str(CONFIG_PATH),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(TRAIN_PATH),
        validation_dataset_path=str(VAL_PATH),
        output_dir=str(output_dir),
        update_source=False,
        verbose=1,
    )


if __name__ == "__main__":
    asyncio.run(main())
