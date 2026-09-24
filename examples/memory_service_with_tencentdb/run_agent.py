#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the TencentDB Agent Memory cross-session example."""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.memory.tencentdb_memory_service import (
    TencentDBMemoryService,
    TencentDBMemoryServiceConfig,
)
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()
sys.path.append(str(Path(__file__).parent))


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


def create_memory_service() -> TencentDBMemoryService:
    """Create the V3 gateway-backed memory service."""
    config = TencentDBMemoryServiceConfig(
        enabled=True,
        endpoint=os.getenv(
            "TENCENTDB_MEMORY_ENDPOINT",
            "http://127.0.0.1:8420",
        ),
        api_key=os.getenv("TENCENTDB_MEMORY_API_KEY", "local"),
        service_id=_required_env("TENCENTDB_MEMORY_SERVICE_ID"),
        team_id=_required_env("TENCENTDB_MEMORY_TEAM_ID"),
        agent_id=_required_env("TENCENTDB_MEMORY_AGENT_ID"),
    )
    return TencentDBMemoryService(config)


async def _run_turn(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    text: str,
) -> None:
    print(f"\nUser ({session_id}): {text}")
    print("Assistant: ", end="", flush=True)
    message = Content(parts=[Part.from_text(text=text)])
    async for event in runner.run_async(
            agent_context=AgentContext(),
            user_id=user_id,
            session_id=session_id,
            new_message=message,
    ):
        if event.partial and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
    print()


async def main() -> None:
    """Write a fact in one session and recall it from another."""
    from agent.agent import root_agent

    memory_service = create_memory_service()
    runner = Runner(
        app_name="tencentdb_memory_demo",
        agent=root_agent,
        session_service=InMemorySessionService(),
        memory_service=memory_service,
    )
    user_id = "alice"

    try:
        await _run_turn(
            runner,
            user_id=user_id,
            session_id="session-write",
            text="My favorite color is blue. Please remember it.",
        )

        wait_seconds = float(os.getenv("TENCENTDB_MEMORY_PIPELINE_WAIT_SECONDS", "5"), )
        print(f"Waiting {wait_seconds:g}s for asynchronous memory extraction...", )
        await asyncio.sleep(wait_seconds)

        await _run_turn(
            runner,
            user_id=user_id,
            session_id="session-recall",
            text="What is my favorite color?",
        )
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
