# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Blackbox CLI example 的优化器入口。

适用场景
--------
业务 agent 是外部命令行工具（trpc-claudecode / claude / codex / 自研 CLI），
其行为由若干 prompt 文件控制。本脚本演示通过 subprocess 把 CLI 当作完全
黑盒的 agent，让 GEPA 优化它读取的 prompt 文件。

这个文件做什么
--------------
1. 注册 workspace/CLAUDE.md + workspace/.claude/skills/city-info/SKILL.md
   两个文件作为 TargetPrompt
2. call_agent 由 agent/call_agent.py 提供（subprocess 调用 CLI + stdout 规范化）
3. 调 AgentOptimizer.optimize 跑 GEPA 反思循环

怎么跑
------
1) 检查 CLI: `which trpc-claudecode`
2) 配 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
3) python examples/optimization/blackbox_cli/run_optimization.py
4) 看 runs/<时间戳>/best_prompts/

接入自有 CLI 时改哪里
---------------------
- agent/call_agent.py 中 CLI_BINARY / 命令行参数 / env 映射
- TargetPrompt.add_path 改为业务 CLI 期望的 prompt 文件路径
- 单文件优化时移除第二个 add_path
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

from agent.call_agent import call_agent  # noqa: E402


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
RUNS_DIR = _HERE / "runs"
WORKSPACE = _HERE / "workspace"
CLAUDE_MD_PATH = WORKSPACE / "CLAUDE.md"
SKILL_MD_PATH = WORKSPACE / ".claude" / "skills" / "city-info" / "SKILL.md"


async def main() -> None:
    """组装双字段 TargetPrompt + 调 AgentOptimizer.optimize。"""
    # CLI 启动时通过 --add-dir <workspace> 自动加载这两个文件。
    # GEPA 把候选写回文件后，下一次 subprocess 启动时 CLI 自动读到新 prompt。
    target = (
        TargetPrompt()
        .add_path("claude_md", str(CLAUDE_MD_PATH))
        .add_path("skill_md", str(SKILL_MD_PATH))
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
