# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Remote Prompt Store example 的优化器入口。

适用场景
--------
业务 prompt 不在本地文件，而由 ops 配在远端配置中心（七彩石 / Apollo /
Nacos / 自研 KV）。本脚本演示通过 TargetPrompt.add_callback 接入用户提供
的 async read / write 函数读写远端，并通过 production / sandbox 双
namespace 隔离生产数据。

这个文件做什么
--------------
1. （演示用）reset_store 把 production + sandbox 都初始化为 baseline
2. 注册 add_callback：优化器通过 read_sandbox_prompt / write_sandbox_prompt
   异步函数与沙箱 namespace 交互
3. 定义 call_agent：每次调用先从 KV 拉最新 prompt 再构造 agent
4. 调 AgentOptimizer.optimize 跑 GEPA 反思循环
5. 收尾时打印生产 / 沙箱 namespace 的状态变化

怎么跑
------
1) 配 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
2) python examples/optimization/remote_prompt_store/run_optimization.py
3) 看 runs/<时间戳>/best_prompts/system_prompt.md（待人工审批）

接入自有配置中心时改哪里
------------------------
- 删除 reset_store(...) 调用（真实业务下生产 namespace 已由 ops 维护）
- 替换 store/prompt_client.py 中 read/write 函数的内部实现为业务 SDK 调用
- update_source=False 严格保持（防生产被未审批变更覆盖）
- 跑完后由人工审批工具把 best_prompts/ 同步到生产
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

from agent.agent import create_agent
from store.prompt_client import (
    PROMPT_KEY_PRODUCTION,
    read_production_prompt,
    read_sandbox_prompt,
    reset_store,
    write_sandbox_prompt,
)


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
RUNS_DIR = _HERE / "runs"
APP_NAME = "remote_prompt_store_demo_agent"

# 演示用 baseline。真实业务里这一步对应"ops 已经在生产 KV 配好 prompt"。
BASELINE_PROMPT = (
    "你是一个友好的聊天助手，喜欢和用户分享想法。回答用户问题时，"
    "请尽量用生动、富有人情味的语言，让用户感觉像是在和朋友聊天。\n"
)


async def call_agent(query: str) -> str:
    """框架回调：从沙箱 KV 拉最新 prompt → 构造 agent → 跑一次推理。

    每次调用都重读 KV，保证优化器写入新候选后立即生效。每次新建
    Runner + InMemorySessionService 给每个 case 独立的 session state，
    并发评测时不互相污染。
    """
    prompt_text = await read_sandbox_prompt()
    agent = create_agent(prompt_text)

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
    """组装 TargetPrompt（add_callback）+ 调 AgentOptimizer.optimize。"""
    # 演示前置：把 KV 重置到"ops 刚配好生产 prompt + 同步到沙箱"的初始态。
    # 真实业务下不需要这一步——业务方的生产 KV 已经有 prompt。
    reset_store(BASELINE_PROMPT)

    # 用 add_callback 而非 add_path：优化器通过两个异步函数与沙箱交互，
    # KV 后端形态对优化器完全黑盒。
    target = TargetPrompt().add_callback(
        "system_prompt",
        read=read_sandbox_prompt,
        write=write_sandbox_prompt,
    )

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = RUNS_DIR / timestamp

    result = await AgentOptimizer.optimize(
        config_path=str(CONFIG_PATH),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(TRAIN_PATH),
        validation_dataset_path=str(VAL_PATH),
        output_dir=str(output_dir),
        # 远端 prompt 场景下严格保持 False：跑完自动把沙箱回滚到 baseline，
        # 生产 namespace 永远不被触碰。最佳候选写到 output_dir/best_prompts/，
        # 由人工审批后通过单独脚本 / 工单流程同步到生产。
        update_source=False,
        verbose=1,
    )

    # 演示"审批后同步"工作流：实际生产中下方逻辑由独立审批工具触发。
    print("\n=== 优化已完成 ===")
    print(f"baseline → best : {result.baseline_pass_rate:.4f} → {result.best_pass_rate:.4f}")
    production_text = await read_production_prompt()
    sandbox_text = await read_sandbox_prompt()
    print(f"\n[KV] production ({PROMPT_KEY_PRODUCTION}) 内容长度: {len(production_text)} 字 (未变)")
    print(f"[KV] sandbox 已自动回滚到 baseline，长度: {len(sandbox_text)} 字")
    print(f"\n请在 {output_dir}/best_prompts/system_prompt.md 查看最佳候选；")
    print("人工审批通过后，再调用 store.prompt_client 中的工具同步到生产。")


if __name__ == "__main__":
    asyncio.run(main())
