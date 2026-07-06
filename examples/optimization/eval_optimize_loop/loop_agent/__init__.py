# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""「城市信息助手」agent 包 —— eval_optimize_loop 专用。

包名刻意叫 ``loop_agent`` 而不是其它 example 常用的 ``agent``：pytest 可能在
同一进程里 import 多个 example 的包，重名会在 ``sys.modules`` 里互相顶掉。

对外暴露两个入口，分别服务闭环的两条链路：

- :func:`get_agent_async` —— ``AgentEvaluator`` 的 ``agent_module`` 模式。
  评测器每次运行都会重新调用它，而它每次都从磁盘重读 prompt 文件，
  所以 pipeline 把候选 prompt 写入源文件后**无需重启进程**即可生效。
  agent_module 模式下 LocalEvalService 能捕获工具轨迹与工具返回 →
  验收套件的 ``tool_trajectory_avg_score`` / ``llm_rubric_knowledge_recall``
  才有数据可评。
- :func:`call_agent` —— ``AgentOptimizer`` 的黑盒回调（query → 最终回答）。
  黑盒模式拿不到工具轨迹，所以 optimizer.json 只配 ``final_response_avg_score``
  （SDK 会硬性拒绝在该模式下配置轨迹/召回类 metric）。

模型是 :class:`~loop_agent.fake_models.FakeAgentModel`（规则驱动、指令敏感、
零 API Key），import 本包时三个 fake provider 已注册进 ModelRegistry。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional, Tuple

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part

from .fake_models import FakeAgentModel, register_fake_models
from .tools import convert_distance, knowledge_search

register_fake_models()

APP_NAME = "eval_optimize_loop_demo"

_PACKAGE_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = _PACKAGE_DIR / "prompts" / "system.md"
SKILL_PATH = _PACKAGE_DIR / "prompts" / "skill.md"


def build_instruction() -> str:
    """从磁盘拼合完整 instruction（system.md + skill.md）。

    每次调用都重读文件：优化器/回归器写入候选 prompt 后立即生效。
    """
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    skill = SKILL_PATH.read_text(encoding="utf-8").strip()
    return f"{system}\n\n{skill}"


def create_agent() -> LlmAgent:
    """用当前磁盘 prompt 构建一个新的 LlmAgent 实例。"""
    return LlmAgent(
        name="city_info_agent",
        description="城市信息助手：距离换算 / 城市介绍 / 身份询问。",
        model=FakeAgentModel("fake-agent/city-info"),
        instruction=build_instruction(),
        tools=[FunctionTool(convert_distance), FunctionTool(knowledge_search)],
    )


async def get_agent_async() -> Tuple[LlmAgent, Optional[object]]:
    """AgentEvaluator agent_module 约定入口：每次评测运行重建 agent。"""
    return create_agent(), None


async def call_agent(query: str) -> str:
    """AgentOptimizer 黑盒回调：驱动 agent 一次，返回最终回答文本。

    与 quickstart 相同的隔离要求：每次调用独立创建 Runner +
    InMemorySessionService，避免并发评测时 session state 互相污染。
    """
    root_agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    session_id = str(uuid.uuid4())
    user_id = "optimizer"
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={},
    )
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
    return final_text.strip()
