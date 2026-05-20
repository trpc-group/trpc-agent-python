# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""配置 B：高阶策略组合 —— frontier_type=objective + skip_perfect_score=true +
use_merge=true。

适用场景
--------
高阶策略 A/B 对照实验的"高阶"运行。与 run_baseline.py 共用同一份数据集
和 agent，仅 optimizer JSON 不同，便于在公平条件下观察策略差异。

预期与 baseline 的差异
----------------------
- 反思 LM 调用更省（满分 case 不再喂回反思 minibatch）
- objective frontier 接受门槛更低，rounds_accepted 更多但 valset 易震荡
- 单字段优化下 use_merge=true 不会真触发 merge（gepa 是 predictor-level
  merge，需要至少 2 个字段才有意义；详见 README §6.1）

输出落到 runs/advanced_<时间戳>/，compare.py 自动选取最新一次对比。
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


CONFIG_PATH = _HERE / "optimizer_advanced.json"
TRAIN_PATH = _HERE / "data" / "train.evalset.json"
VAL_PATH = _HERE / "data" / "val.evalset.json"
RUNS_DIR = _HERE / "runs"


async def main() -> None:
    """组装 TargetPrompt + 调 AgentOptimizer.optimize（用 advanced 配置）。"""
    target = TargetPrompt().add_path("system_prompt", str(SYSTEM_PROMPT_PATH))

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = RUNS_DIR / f"advanced_{timestamp}"

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
