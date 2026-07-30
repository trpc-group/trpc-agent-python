# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CI Integration example 的夜间优化入口。

适用场景
--------
CI/CD 流水线中的夜间窗口任务：跑 GEPA 反思优化，最优候选自动写回源
prompt 文件，下一次 PR 触发的 pytest 守门自动用上新 prompt → 形成
"评测 → 优化 → 再评测"的演进闭环。

这个文件做什么
--------------
1. 注册 system.md + skill.md 双字段 TargetPrompt
2. 引用 agent/agent.py 中**与 pytest 共享**的 call_agent
3. 以 update_source=True 跑优化，最优候选自动覆盖源 prompt 文件

怎么跑
------
通过 shell 入口（CI 流水线建议方式）：
    PYTHONPATH=../../.. bash ci/run_nightly_optimize.sh

直接跑：
    python examples/optimization/ci_integration/run_optimization.py

关键设计
--------
本脚本与 tests/test_agent_quality.py 共享：
- 同一个 agent/ 包（同一个 call_agent + 同一对 prompt 文件）
- 同一份 evalset 数据资产（物理拆 train / val 两文件，schema 一致）
- 同一套 metric 定义（schema 一致）
保证 PR 守门用的 agent 与夜间优化用的 agent 等价。

接入自有 CI 时改哪里
--------------------
- agent/agent.py 改为业务 call_agent（pytest 与本脚本同时引用）
- update_source=True 严格保持（CI 闭环的关键）
- 末尾建议加 git diff agent/prompts/ + 自动开 PR 步骤
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

from agent.agent import SKILL_PATH, SYSTEM_PROMPT_PATH, call_agent  # noqa: E402


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "data" / "train.evalset.json"
VAL_PATH = _HERE / "data" / "val.evalset.json"
RUNS_DIR = _HERE / "runs"


async def main() -> None:
    """组装 TargetPrompt + 调 AgentOptimizer.optimize（update_source=True）。"""
    target = (
        TargetPrompt()
        .add_path("system_prompt", str(SYSTEM_PROMPT_PATH))
        .add_path("skill", str(SKILL_PATH))
    )

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = RUNS_DIR / f"optimize_{timestamp}"

    await AgentOptimizer.optimize(
        config_path=str(CONFIG_PATH),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(TRAIN_PATH),
        validation_dataset_path=str(VAL_PATH),
        output_dir=str(output_dir),
        # update_source=True：优化成功后最优候选直接写回 agent/prompts/。
        # CI 闭环的关键开关——下一次 PR 触发的 pytest 自动用上新 prompt。
        # 仅在 OptimizeResult.status=SUCCEEDED 时才会写回；失败 / 预算耗尽
        # 等情况下源文件保持不变。
        update_source=True,
        verbose=1,
    )


if __name__ == "__main__":
    asyncio.run(main())
