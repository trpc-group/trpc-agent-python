# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""API 摘要 Agent —— evaluate 与 optimize 双链路共享的统一入口。

适用场景
--------
CI/CD 闭环的核心约束：评测时的 agent 与优化时的 agent 必须等价。本文件
作为 PR 守门（pytest）与夜间优化（AgentOptimizer.optimize）共享的
call_agent 实现，保证两条链路看到相同 agent 行为。

这个文件做什么
--------------
1. 暴露 SYSTEM_PROMPT_PATH / SKILL_PATH 作为 TargetPrompt 注册目标
2. 提供 call_agent 黑盒入口（被 pytest + optimizer 同时调用）
3. 用 _normalize_json 把 LLM 输出规范化为稳定 JSON 字符串，使 metric
   走 text exact 而非依赖 LLM judge——CI 上快、稳、可重复

为什么 evaluate 与 optimize 要共享 call_agent
---------------------------------------------
通过共享同一份代码，保证任何 agent 行为改动（模型切换、temperature 调整、
output schema 变化）只需改一处，PR 守门与夜间优化同时生效。否则会出现
"优化器找到了 evaluator 验证不了的 prompt"这种链路失配问题。
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
SKILL_PATH = Path(__file__).parent / "prompts" / "skill.md"

APP_NAME = "ci_integration_demo"

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _create_model() -> LLMModel:
    """构建 OpenAI 兼容 chat 模型实例。"""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _read_instruction() -> str:
    """从两个 prompt 文件拼合完整 instruction。

    每次调用都重读磁盘——夜间优化阶段 GEPA 把候选写到磁盘后下一次推理
    立即生效；PR 阶段拿到的也是最新已落盘的版本。
    """
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    skill = SKILL_PATH.read_text(encoding="utf-8").strip()
    return f"{system}\n\n## How to write the summary\n{skill}"


def create_agent() -> LlmAgent:
    """构建一个使用当前磁盘 prompt 的新 LlmAgent 实例。"""
    return LlmAgent(
        name="api_summarizer",
        description="Summarizes a RESTful API description into a strict JSON.",
        model=_create_model(),
        instruction=_read_instruction(),
        generate_content_config=GenerateContentConfig(
            temperature=0.1,
            top_p=0.9,
            max_output_tokens=512,
        ),
    )


# 兼容 agent_module="agent" 加载约定（root_agent）。
# AgentEvaluator 在 call_agent 模式下并不需要它，但保留无害，方便切换形态。
root_agent = create_agent()


def _normalize_json(raw: str) -> str:
    """把 LLM 输出规范化成稳定 JSON 字符串。

    步骤：
    1. 用正则定位首个 {...} 块（兼容模型偶尔在 JSON 前后多吐字符）
    2. json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))
       消除空格 / key 顺序差异
    3. 解析失败时原样返回（让 metric 看到 "garbage" → 0 分）

    经过本函数后 baseline / 候选 prompt / evalset 期望值都对齐到唯一
    字符串形态，可直接走 final_response_avg_score(text.match=exact)。
    CI 上**完全不依赖 LLM judge**，速度与稳定性显著提升。
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
    """供 evaluate / optimize 共享的黑盒 agent 入口。

    每次调用都重新构建 Runner + InMemorySessionService，给每个 case 独立的
    session state，并发评测时不互相污染。
    """
    root = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        app_name=APP_NAME,
        agent=root,
        session_service=session_service,
    )
    session_id = str(uuid.uuid4())
    user_id = "ci"
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
