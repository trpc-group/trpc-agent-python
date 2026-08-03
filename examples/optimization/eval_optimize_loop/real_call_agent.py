# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""真实 LLM 的 call_agent（接入 hy3 等 OpenAI 兼容后端）。

与 fake_agent 保持一致的交互契约：
- pipeline 在每轮评估前调用 set_prompt(text) 写入"当前生效的 prompt"；
- call_agent(query) 用该 prompt 作为 system instruction 跑一次真实推理。

这样 pipeline 既能用确定性 fake_agent，也能无缝切到真实模型，而优化器
（RuleBasedOptimizer，等价扩展机制）与 gate / 归因 / 审计逻辑无需改动。

评测判定由 config/real_optimizer.json 里的 llm_rubric_response 指标完成，
judge 同样走 hy3（OpenAI 兼容），无需任何本地规则。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part

_APP_NAME = "eval_optimize_loop_real"

# 模块级"当前生效 prompt"，由 pipeline 在每轮优化时更新（与 fake_agent 同契约）。
_CURRENT_PROMPT = [""]
_MODEL = None


def _model_config() -> tuple[str, str, str]:
    ak = os.getenv("TRPC_AGENT_API_KEY", "")
    bu = os.getenv("TRPC_AGENT_BASE_URL", "")
    mn = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not ak or not bu or not mn:
        raise ValueError(
            "请先配置环境变量 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / "
            "TRPC_AGENT_MODEL_NAME（可参考 .env.example，或用真实 .env）。"
        )
    return ak, bu, mn


def _get_model() -> OpenAIModel:
    """懒加载并缓存模型实例，避免每次调用都重建。"""
    global _MODEL
    if _MODEL is None:
        ak, bu, mn = _model_config()
        _MODEL = OpenAIModel(model_name=mn, api_key=ak, base_url=bu)
    return _MODEL


def set_prompt(text: str) -> None:
    """pipeline 写入本轮候选 prompt，使后续 call_agent 使用它。"""
    _CURRENT_PROMPT[0] = text or ""


def get_prompt() -> str:
    return _CURRENT_PROMPT[0]


async def call_agent(query: str) -> str:
    """框架回调：用真实 LLM 跑一次推理，返回最终文本。"""
    # 节流：两次生成之间稍作停顿，降低对 hy3 的瞬时并发，规避 429 限流。
    await asyncio.sleep(0.6)
    agent = LlmAgent(
        name="eval_agent",
        description="Evaluated agent backed by a real LLM (hy3).",
        model=_get_model(),
        instruction=_CURRENT_PROMPT[0],
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=1024,
        ),
    )
    session_service = InMemorySessionService()
    runner = Runner(app_name=_APP_NAME, agent=agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=_APP_NAME, user_id="eval", session_id=session_id, state={}
    )
    user_content = Content(role="user", parts=[Part.from_text(text=query)])

    final_text = ""
    async for event in runner.run_async(
        user_id="eval", session_id=session_id, new_message=user_content
    ):
        if not event.is_final_response():
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if getattr(part, "thought", False):
                continue
            if part.text:
                final_text += part.text
    return final_text
