#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run a cross-session Hy-Memory example."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from hy_memory import HyMemoryClient

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.memory import HyMemoryService
from trpc_agent_sdk.memory import HyMemoryServiceConfig
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import load_memory_tool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from custom_embedder import BgeM3RemoteEmbeddingService

load_dotenv(Path(__file__).with_name(".env"))


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def create_agent() -> LlmAgent:
    """Create an agent that explicitly recalls long-term memory."""
    model = OpenAIModel(
        model_name=_required_env("TRPC_AGENT_MODEL_NAME"),
        api_key=_required_env("TRPC_AGENT_API_KEY"),
        base_url=_required_env("TRPC_AGENT_BASE_URL"),
    )
    return LlmAgent(
        name="hy_memory_assistant",
        description="An assistant with cross-session long-term memory.",
        model=model,
        instruction=("Long-term memory is stored automatically after each turn; no "
                     "save-memory tool call is needed. Use load_memory before answering "
                     "questions about earlier conversations. Clearly say when no "
                     "relevant memory exists."),
        tools=[load_memory_tool],
    )


def create_memory_service() -> tuple[HyMemoryService, HyMemoryClient]:
    """Create Hy-Memory with the remote BGE-M3 embedding service."""
    scene = os.getenv("HY_MEMORY_SCENE", "chat").strip() or None
    client = HyMemoryClient(
        mode=os.getenv("MEMORY_MODE", "pro"),
        embedding_service=BgeM3RemoteEmbeddingService.from_env(),
    )
    memory_service = HyMemoryService(
        HyMemoryServiceConfig(
            enabled=True,
            mode=os.getenv("MEMORY_MODE", "pro"),
            scene=scene,
            source="trpc-agent-example",
            workspace_id=os.getenv("HY_MEMORY_WORKSPACE_ID") or None,
            branch=os.getenv("HY_MEMORY_BRANCH") or None,
        ),
        client=client,
    )
    return memory_service, client


async def ask(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    query: str,
) -> None:
    """Run one query and print the final response."""
    print(f"\nUser ({session_id}): {query}")
    print("Assistant: ", end="", flush=True)
    message = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(agent_context=AgentContext(),
                                        user_id=user_id,
                                        session_id=session_id,
                                        new_message=message):
        if event.partial and event.content:
            for part in event.content.parts or []:
                if part.text and not part.thought:
                    print(part.text, end="", flush=True)
    print()


async def main() -> None:
    """Write a preference in one session and recall it in another."""
    user_id = (os.getenv("HY_MEMORY_DEMO_USER_ID", "").strip() or f"demo-user-{uuid.uuid4().hex[:8]}")
    print(f"Hy-Memory demo user: {user_id}")
    memory_service, hy_memory_client = create_memory_service()
    runner = Runner(
        app_name="hy_memory_example",
        agent=create_agent(),
        session_service=InMemorySessionService(),
        memory_service=memory_service,
    )
    try:
        await ask(
            runner,
            user_id=user_id,
            session_id="write-session",
            query=("Please remember that I prefer concise answers and use "
                   "Python for backend development."),
        )
        await ask(
            runner,
            user_id=user_id,
            session_id="recall-session",
            query="What response style and backend language do I prefer?",
        )
    finally:
        await runner.close()
        # Injected clients are caller-owned and are not closed by the service.
        await asyncio.to_thread(hy_memory_client.close)


if __name__ == "__main__":
    asyncio.run(main())
