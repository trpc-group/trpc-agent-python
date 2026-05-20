# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Multi-Metric with Judges example 的优化器入口。

适用场景
--------
业务 agent 同时受多类约束（答案正确性硬约束 + 风格 / 安全 / 合规软约束），
需要多条 metric 共同参与优化与早停判定，并希望通过多 judge 投票降低单
LLM 裁判的偏差。

这个文件做什么
--------------
1. 注册单字段 TargetPrompt（agent/prompts/system.md）
2. 定义 call_agent 用当前 prompt 跑一次推理
3. 调 AgentOptimizer.optimize；具体 multi-metric / multi-judge 配置在
   optimizer.json 中

怎么跑
------
1) 配 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
2) python examples/optimization/multi_metric_with_judges/run_optimization.py
3) 单次约 5-10 分钟，每条 case 约 (3+1)×num_runs=2 = 8 次 LLM 调用

接入自有业务时改哪里
--------------------
本脚本本身基本不变，主要改动在 optimizer.json：
- evaluate.metrics：列出业务的多条 metric
- judge_models 数组形式 + models_aggregator 选择投票策略
- frontier_type="hybrid" 多 metric 推荐
- stop.required_metrics 决定哪些 metric 参与早停
- eval_case_parallelism 控制 multi-judge 并发避免 rate limit
详见 README §5。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from agent.agent import SYSTEM_PROMPT_PATH, create_agent


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
RUNS_DIR = _HERE / "runs"
APP_NAME = "multi_metric_demo_agent"


async def call_agent(query: str) -> str:
    """框架回调：用当前 system.md 构造 LlmAgent，跑一次推理。

    每次调用都重读 prompt + 新建 Runner + InMemorySessionService，给每个
    case 独立的 session state，并发评测时不互相污染。
    """
    agent = create_agent()

    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "optimizer"
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id, state={},
    )
    user_content = Content(role="user", parts=[Part.from_text(text=query)])

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=user_content,
    ):
        if not event.is_final_response():
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.thought:
                continue
            if part.text:
                final_text += part.text
    return final_text.strip()


async def main() -> None:
    """组装 TargetPrompt + 调 AgentOptimizer.optimize。"""
    target = TargetPrompt().add_path("system_prompt", str(SYSTEM_PROMPT_PATH))

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
