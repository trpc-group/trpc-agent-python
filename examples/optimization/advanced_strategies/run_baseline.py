# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""配置 A：basic 策略组合 —— 与 quickstart 几乎一致，作为对照基线。

适用场景
--------
高阶策略 A/B 对照实验的基线运行。配合 run_advanced.py + compare.py 使用：
- 本脚本：basic 策略组合（pareto + instance + use_merge=false +
  skip_perfect_score=false）
- run_advanced.py：高阶策略组合
- compare.py：解析两次 result.json 输出对比表

输出落到 runs/baseline_<时间戳>/，compare.py 自动选取最新一次对比。
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

from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt  # noqa: E402

from agent.agent import SYSTEM_PROMPT_PATH, call_agent  # noqa: E402


CONFIG_PATH = _HERE / "optimizer_baseline.json"
TRAIN_PATH = _HERE / "data" / "train.evalset.json"
VAL_PATH = _HERE / "data" / "val.evalset.json"
RUNS_DIR = _HERE / "runs"


async def main() -> None:
    """组装 TargetPrompt + 调 AgentOptimizer.optimize（用 baseline 配置）。"""
    target = TargetPrompt().add_path("system_prompt", str(SYSTEM_PROMPT_PATH))

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = RUNS_DIR / f"baseline_{timestamp}"

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
