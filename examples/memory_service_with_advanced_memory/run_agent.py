#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the two-session Advanced Memory demonstration."""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.memory import AdvancedMemoryConfig
from trpc_agent_sdk.sessions import AdvancedMemorySessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from agent.agent import create_agent

load_dotenv(Path(__file__).with_name(".env"))


def create_session_service() -> AdvancedMemorySessionService:
    """Create the persistent Advanced Memory session service."""
    memory_ttl = os.getenv("M_TTL")
    session_ttl = os.getenv("SESSION_TTL")
    session_ttl_seconds = int(session_ttl) if session_ttl else 0
    return AdvancedMemorySessionService(
        config=AdvancedMemoryConfig(
            root_dir=Path(__file__).resolve().parent,
            memory_ttl_seconds=int(memory_ttl) if memory_ttl else None,
            session_ttl_seconds=session_ttl_seconds or None,
            memory_focus_instruction=(
                "特别关注并主动记住用户长期稳定的兴趣爱好、"
                "编程语言偏好、开发习惯和测试习惯。"
            ),
        ),
        session_config=SessionServiceConfig(
            ttl=SessionServiceConfig.create_ttl_config(
                enable=bool(session_ttl),
                ttl_seconds=session_ttl_seconds,
                cleanup_interval_seconds=5,
            ),
        ),
    )


async def run_turn(runner, *, user_id: str, session_id: str, prompt: str) -> None:
    """Run one turn and print tool activity and the final response."""
    print(f"\n👤 [{session_id}] {prompt}")
    content = Content(parts=[Part.from_text(text=prompt)])
    async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.function_call:
                print(f"🔧 {part.function_call.name}({part.function_call.args})")
            elif part.function_response:
                print(f"📊 {part.function_response.response}")
            elif part.text and not part.thought and not event.partial:
                print(f"🤖 {part.text}")


async def main() -> None:
    """Run two independent sessions sharing Advanced Memory."""
    agent = create_agent()
    session_service = create_session_service()

    from trpc_agent_sdk.runners import Runner
    runner = Runner(
        app_name="advanced_memory_demo",
        agent=agent,
        session_service=session_service,
    )
    memory_ttl = os.getenv("M_TTL")
    memory_ttl_seconds = int(memory_ttl) if memory_ttl else 0
    session_ttl = os.getenv("SESSION_TTL")
    session_ttl_seconds = int(session_ttl) if session_ttl else 0
    try:
        session_one_prompts = [
            ("Please remember that my favorite programming language is Python. "
             "Save this as a user preference."),
            "I use Python mainly for backend services and data processing.",
            "I prefer typed Python code with clear dataclasses and small modules.",
            "For testing Python code, I usually prefer pytest and focused unit tests.",
            "When documenting projects, I prefer concise examples with runnable commands.",
        ]
        for prompt in session_one_prompts:
            await run_turn(
                runner,
                user_id="demo-user",
                session_id="session-1",
                prompt=prompt,
            )

        await run_turn(
            runner,
            user_id="demo-user",
            session_id="session-1",
            prompt="Summarize what you learned about my Python development preferences.",
        )

        await run_turn(
            runner,
            user_id="demo-user",
            session_id="session-2",
            prompt="What do you remember about my favorite programming language?",
        )

        wait_seconds = max(memory_ttl_seconds, session_ttl_seconds)
        if wait_seconds:
            print(f"\n⏳ Waiting for TTL cleanup ({wait_seconds + 5}s)...")
            await asyncio.sleep(wait_seconds + 5)
            print("🧹 Expired Advanced Memory data should now be removed.")
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
