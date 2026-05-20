# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""地址解析 agent —— Advanced Strategies example 专用。

任务设计动机
------------
本 example 用于验证 GEPA 高阶策略组合（use_merge / frontier_type /
skip_perfect_score 等）的真实效果。任务必须存在两个**互相牵制**的维度，
才能逼出策略差异：

A. 完整地址（country/city/postal_code/street 都给到）→ 期望严格 JSON
B. 缺信息地址（少 postal_code 或 street）→ 期望对应字段输出 null

候选 prompt 容易陷入两个局部最优：
- 候选 P1 学会"严格 JSON"但所有字段都不给 null（缺信息时硬编一个）
- 候选 P2 学会"该 null 就 null"但 JSON 格式偶尔崩

→ 多字段场景下 use_merge=true 能融合 P1/P2 各自掌握的子能力。
→ frontier_type 选 instance vs objective 在这类任务上行为差异显著。

接入业务时改哪里
----------------
- 替换为业务任务 agent 与 prompt
- 保留 _normalize_json 让 metric 走 text exact，CI 上更稳
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part

from .config import get_model_config


SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
APP_NAME = "advanced_strategies_demo"

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _create_model() -> LLMModel:
    """构建 OpenAI 兼容 chat 模型实例。"""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _read_instruction() -> str:
    """从磁盘重读 system.md。"""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def create_agent() -> LlmAgent:
    """构建一个使用当前磁盘 prompt 的新 LlmAgent 实例。"""
    return LlmAgent(
        name="address_parser",
        description="Parses free-text postal addresses into a strict JSON.",
        model=_create_model(),
        instruction=_read_instruction(),
        generate_content_config=GenerateContentConfig(
            temperature=0.1,
            top_p=0.9,
            max_output_tokens=256,
        ),
    )


def _normalize_json(raw: str) -> str:
    """把 LLM 输出规范化成稳定 JSON 字符串。

    与 ci_integration / blackbox_cli 完全相同的规范化逻辑：让
    final_response_avg_score(text.match=exact) 直接走精确匹配。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return text
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return text
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


async def call_agent(query: str) -> str:
    """框架回调：跑一次推理，输出经 _normalize_json 规范化。"""
    root = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        app_name=APP_NAME,
        agent=root,
        session_service=session_service,
    )
    session_id = str(uuid.uuid4())
    user_id = "parser"
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={},
    )
    user_content = Content(role="user", parts=[Part.from_text(text=query)])

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
            if part.thought:
                continue
            if part.text:
                final_text += part.text
    return _normalize_json(final_text)
