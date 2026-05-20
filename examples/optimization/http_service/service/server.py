# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""HTTP Service example 的 mock 线上 agent 服务。

适用场景
--------
模拟"业务方已有的 HTTP agent 服务"，作为优化器对接的目标。本文件存在
仅为让 example 自包含可跑；真实接入时业务方应已有同等形态的 HTTP 服务。

这个文件做什么
--------------
- 暴露 GET /health 健康检查端点
- 暴露 POST /chat 单次推理端点：收 {"query": "..."}，返回 {"final_text": "..."}
- 在每次 /chat 请求时**重读 prompts/system.md**，使优化器写入的新候选
  下一次请求即生效（即"prompt 热加载"）

怎么跑
------
1) 配 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
2) python examples/optimization/http_service/service/server.py
3) 服务监听 http://127.0.0.1:8767，保持终端运行，再启动优化器

prompt 热加载是核心约束
-----------------------
优化器通过磁盘文件给服务"喂"新候选 prompt。如果服务把 prompt 缓存在
进程内存，优化器改了文件也没用，整个反思循环失去意义。
本文件通过 _build_agent() 在每次 /chat 都重读磁盘实现该语义。

接入业务真实服务时改哪里
------------------------
真实业务下整体不需要本文件，由实际 HTTP 服务承担相同角色。需保证：
- 服务在每次请求处理前重读 prompt 文件（或重新拉配置中心）
- 响应字段与 run_optimization.py 中 call_agent 的解析逻辑对齐
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


SYSTEM_PROMPT_PATH = _HERE / "prompts" / "system.md"
APP_NAME = "http_service_demo_agent"
HOST = "127.0.0.1"
PORT = 8767


class ChatRequest(BaseModel):
    query: str


class ChatResponse(BaseModel):
    final_text: str


def _read_system_prompt() -> str:
    """从磁盘重读 system prompt——优化器写入的最新候选才会立即生效。"""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def _build_agent() -> LlmAgent:
    """用当前磁盘上的 system prompt 构造一个全新的 LlmAgent 实例。

    凭据缺任意一个就 fail-fast，避免运行到一半才撞到 LLM 后端的 401 错误
    （那时报错信息会很有迷惑性，看起来像 prompt 问题）。
    """
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise RuntimeError(
            "TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME "
            "must be set before starting the HTTP service."
        )
    return LlmAgent(
        name="math_word_problem_agent",
        description="Math word-problem solver served over HTTP.",
        model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url),
        instruction=_read_system_prompt(),
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=2048,
        ),
    )


app = FastAPI(title="http_service demo")


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查端点：优化器启动前 ping 一次确认服务已就绪。"""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """单次推理。每次都新建 Runner + InMemorySession + 重读 prompt。

    无状态设计：优化器可能并发评测多条 case，共享 session 会导致上下文
    污染。每次请求重建 LlmAgent 也意味着每次都重读 system.md，正是
    优化器写入新候选后能立即生效的关键。
    """
    agent = _build_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "http_client"
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id, state={},
    )
    user_content = Content(role="user", parts=[Part.from_text(text=request.query)])

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=user_content,
    ):
        if not event.is_final_response():
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.thought:           # 跳过 thinking token
                continue
            if part.text:
                final_text += part.text
    return ChatResponse(final_text=final_text.strip())


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
