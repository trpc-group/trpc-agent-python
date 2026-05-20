# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Quickstart 入口脚本：演示用 GEPA 同时优化两个 prompt 文件。

适用场景
--------
你想跑通 prompt 自动优化的最小完整流程：让一个反思 LLM 看你 agent 的失败用例，
自动改写 prompt 直到通过率达标。本脚本是 10 个 example 的入门款。

这个文件做什么
--------------
1. 注册两个 prompt 文件作为优化目标（system.md + skill.md）
2. 定义 call_agent 回调（框架通过它驱动 agent）
3. 调 AgentOptimizer.optimize 开跑

怎么跑
------
1) 配三个环境变量：TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
2) python examples/optimization/quickstart/run_optimization.py
3) 看 runs/<时间戳>/ 下的 summary.txt 和 best_prompts/

接入自己业务时改哪里
--------------------
- target              : 改成你自己的 prompt 文件路径（main 函数内）
- call_agent          : 替换实现，让它调你的 agent（HTTP / 多 agent 链路 /
                        远端 prompt 等其他形态见对应 example）
- update_source=False : 想跑完直接覆盖源文件改 True（典型 CI 场景）
- verbose             : 0 静默 / 1 进度面板 / 2 加 gepa 内部日志
- CONFIG_PATH         : 算法和 metric 配置都在 optimizer.json
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path


# ---- 路径自举：让脚本在任意 cwd 下都能运行 ----
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

from agent.agent import SKILL_PATH, SYSTEM_PROMPT_PATH, create_agent


# ---- 配置与数据路径 ----
CONFIG_PATH = _HERE / "optimizer.json"          # 算法 + metric 配置
TRAIN_PATH = _HERE / "train.evalset.json"       # 反思时的 minibatch 来源（5 条算术题）
VAL_PATH = _HERE / "val.evalset.json"           # 每轮全量评估，决定是否接受候选
RUNS_DIR = _HERE / "runs"                       # 每次运行写到独立时间戳子目录
APP_NAME = "math_word_problem_optimizer"        # Runner / SessionService 的命名空间


async def call_agent(query: str) -> str:
    """框架回调：用当前候选 prompt 驱动 agent 一次，返回最终回答文本。

    框架在以下时机会调用本函数：
    - baseline 评估：每条 val case × num_runs 次
    - 每轮反思：每条 minibatch case 评测一次
    - 每轮验证：每条 val case × num_runs 次

    实现要点
    --------
    1. 每次调用都从磁盘重读 prompt → GEPA 写入新候选后立即生效，无需重启进程
    2. 每次调用独立创建 Runner + InMemorySessionService → 每个 case 拿到全新
       session state，并发评测时不互相污染（评估隔离的硬性要求）
    3. 只收集 is_final_response() 事件中非 thought 的文本 → 过滤掉 thinking
       token，只返回正式回答

    参数
    ----
    query: 用户输入文本（来自 evalset 的 conversation[*].user_content）

    返回
    ----
    agent 最终回答的纯文本（已 strip）
    """
    # 每次调用重读 prompt 文件（在 create_agent() 内部完成）
    root_agent = create_agent()

    # 每个 case 一份独立的 session 服务，保证并发评测时不会通过 session
    # state 互相污染评分。
    session_service = InMemorySessionService()
    runner = Runner(
        app_name=APP_NAME,
        agent=root_agent,
        session_service=session_service,
    )

    session_id = str(uuid.uuid4())
    user_id = "optimizer"
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={},
    )
    user_content = Content(role="user", parts=[Part.from_text(text=query)])

    # 收集最终回答；过滤掉 thinking token（如果模型启用了 think 模式）
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_content,
    ):
        if not event.is_final_response():
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.thought:           # 跳过 thinking，只保留正式回答
                continue
            if part.text:
                final_text += part.text
    return final_text.strip()


async def main() -> None:
    """组装 TargetPrompt + 调 AgentOptimizer.optimize。"""

    # 注册两个优化目标文件。
    # GEPA 的 round_robin module_selector 会每轮交替选其中一个改写——
    # 单轮只改一个文件能让反思 LM 更聚焦，也容易归因"是哪个文件提升了效果"。
    target = (
        TargetPrompt()
        .add_path("system_prompt", str(SYSTEM_PROMPT_PATH))
        .add_path("skill", str(SKILL_PATH))
    )

    # 每次运行落到独立目录，重复运行不覆盖历史结果
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = RUNS_DIR / timestamp

    await AgentOptimizer.optimize(
        config_path=str(CONFIG_PATH),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(TRAIN_PATH),
        validation_dataset_path=str(VAL_PATH),
        output_dir=str(output_dir),
        # update_source=False：源 prompt 文件保持不变，最优候选只写到
        # output_dir/best_prompts/。改 True 则在 SUCCEEDED 后覆盖源文件，
        # 适用于"跑完直接用"的 CI 场景（参考 ci_integration/ example）。
        update_source=False,
        # verbose: 0 静默；1 Rich 进度面板；2 附带 gepa 内部诊断日志
        verbose=1,
    )


if __name__ == "__main__":
    asyncio.run(main())
