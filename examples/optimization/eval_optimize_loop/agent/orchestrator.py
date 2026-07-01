# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""真实模式的多 agent 链路：router → solver(system + skill) → 最终答复。

链路形态::

    用户问题 → router(分流) → solver(用 system.md 定格式 + skill.md 定题型) → 答复

这是 real 模式（配了 TRPC_AGENT_* 时）真正驱动的 agent。三个 prompt 文件正好
对应 TargetPrompt 的三个优化字段：router / system_prompt / skill。

prompt 热加载：每次 invoke 都重读 prompt 文件——优化器写入候选后下一次调用
即生效，无需重启进程。

fake 模式不会用到本文件，改用 :mod:`agent.fake_backend` 的确定性求解器。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part

from .config import get_model_config


_PROMPTS_DIR = Path(__file__).parent / "prompts"
ROUTER_PROMPT_PATH = _PROMPTS_DIR / "router.md"
SYSTEM_PROMPT_PATH = _PROMPTS_DIR / "system.md"
SKILL_PROMPT_PATH = _PROMPTS_DIR / "skill.md"

APP_NAME = "eval_optimize_loop"


def _create_agent(name: str, instruction: str) -> LlmAgent:
    """构造一个 LlmAgent，instruction 由调用方现读现拼。"""
    api_key, base_url, model_name = get_model_config()
    return LlmAgent(
        name=name,
        description=f"eval_optimize_loop {name}",
        model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url),
        instruction=instruction,
        generate_content_config=GenerateContentConfig(
            temperature=0.2, top_p=0.9, max_output_tokens=1024,
        ),
    )


async def _run_one(agent: LlmAgent, user_text: str) -> str:
    """跑一个 agent 拿最终回答；每次独立 Runner/Session 保证评测隔离。"""
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "pipeline"
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id, state={},
    )
    user_content = Content(role="user", parts=[Part.from_text(text=user_text)])

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


async def call_agent_real(query: str) -> str:
    """框架回调（real 版）：把 query 跑过整条链路，返回最终答复。"""
    # 1. router：判定题型（每次重读 router.md）
    router = _create_agent("router", ROUTER_PROMPT_PATH.read_text(encoding="utf-8").strip())
    await _run_one(router, f"用户问题：{query}\n\n请判断这是加法、乘法还是折扣问题。")

    # 2. solver：system.md（格式约束）+ skill.md（题型能力）拼成 instruction
    solver_instruction = (
        SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
        + "\n\n## 解题技能\n"
        + SKILL_PROMPT_PATH.read_text(encoding="utf-8").strip()
    )
    solver = _create_agent("solver", solver_instruction)
    return await _run_one(solver, query)
