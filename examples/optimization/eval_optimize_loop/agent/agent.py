# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""图书馆藏查询 agent（online 模式被测 agent）。

关键设计：call_agent 每次调用都 create_agent() → _read_instruction()，从磁盘**重读**
system.md。AgentOptimizer.optimize 每轮通过 TargetPrompt.write_all() 把候选 prompt 原子写入
system.md，下一轮 call_agent 自然读到新 prompt —— 这就是「prompt 热加载」，让候选 prompt
真实改变 agent 行为（fake/trace 模式则用预录制 actual，见 offline/fixtures.py）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, GenerateContentConfig, Part

from .config import get_model_config
from .tools import get_order_status

SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
APP_NAME = "eval_optimize_loop"


def _create_model() -> OpenAIModel:
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _read_instruction() -> str:
    """从磁盘重读 system.md（热加载入口）。"""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def create_agent() -> LlmAgent:
    """构建使用当前磁盘 prompt 的新 LlmAgent 实例。"""
    return LlmAgent(
        name="library_catalog",
        description="图书馆藏查询 agent：图书分类 + 馆藏查询 + JSON 输出",
        model=_create_model(),
        instruction=_read_instruction(),
        tools=[get_order_status],
        generate_content_config=GenerateContentConfig(temperature=0.1, top_p=0.9, max_output_tokens=256),
    )


async def call_agent(query: str) -> str:
    """框架回调：跑一次真实推理，返回 final response 文本。"""
    root = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "user"
    await session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id, state={})
    user_content = Content(role="user", parts=[Part.from_text(text=query)])
    final_text = ""
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        if not event.is_final_response():
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.thought:
                continue
            if part.text:
                final_text += part.text
    return final_text
