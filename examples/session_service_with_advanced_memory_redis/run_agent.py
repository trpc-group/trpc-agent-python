#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run native Session compaction over the standard RedisSessionService."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizer
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerConfig
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerManager
from trpc_agent_sdk.sessions.compact import AutoCompactSummarizerConfig
from trpc_agent_sdk.sessions.compact import SessionMemoryExtractorConfig
from trpc_agent_sdk.sessions.compact import TokenContextTrackerConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


def redis_url() -> str:
    """Build the Redis connection URL from environment variables."""
    db_user = os.environ.get("REDIS_USER", "")
    db_password = os.environ.get("REDIS_PASSWORD", "")
    db_host = os.environ.get("REDIS_HOST", "127.0.0.1")
    db_port = os.environ.get("REDIS_PORT", "6379")
    db_name = os.environ.get("REDIS_DB", "0")

    if db_password:
        if db_user:
            return f"redis://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        return f"redis://:{db_password}@{db_host}:{db_port}/{db_name}"
    return f"redis://{db_host}:{db_port}/{db_name}"


def create_compact_config() -> AdvancedAutoCompactSummarizerConfig:
    """Configure only the settings needed to demonstrate one compaction."""
    return AdvancedAutoCompactSummarizerConfig(
        token_context_tracker=TokenContextTrackerConfig(
            model_context_window_tokens=4096,
            max_output_tokens=256,
            warning_ratio=0.25,
            auto_compact_ratio=0.30,
            blocking_ratio=0.95,
        ),
        session_memory=SessionMemoryExtractorConfig(
            initial_tokens=500,
            update_tokens=500,
        ),
        auto_compact=AutoCompactSummarizerConfig(keep_recent_contents=2),
    )


async def main() -> None:
    """Attach Session Compact to RedisSessionService and run the demo."""
    app_name = "session-service-advanced-memory-redis"
    user_id = "demo-user"
    session_id = os.getenv("SESSION_ID", "simple-demo")
    from agent.agent import create_agent

    agent = create_agent()
    compact_config = create_compact_config()
    compact_manager = AdvancedAutoCompactSummarizerManager(
        AdvancedAutoCompactSummarizer(compact_config),
    )
    session_config = SessionServiceConfig(store_historical_events=True)
    session_service = RedisSessionService(
        db_url=redis_url(),
        is_async=True,
        session_config=session_config,
        summarizer_manager=compact_manager,
    )
    runner = Runner(
        app_name=app_name,
        agent=agent,
        session_service=session_service,
    )
    try:
        for prompt in (
                "Generate a large report about Redis session persistence.",
                "What are the key points and persistence options?",
                "List the main operational risks and mitigations.",
                "Summarize our work so far and preserve the important state.",
        ):
            print(f"\nUser: {prompt}")
            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=Content(parts=[Part.from_text(text=prompt)]),
            ):
                if event.content and not event.partial:
                    for part in event.content.parts:
                        if part.text and not part.thought:
                            print(f"Assistant: {part.text}")

        stored = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if stored is not None:
            print(f"\nActive Events: {len(stored.events)}")
            print(f"Historical Events: {len(stored.historical_events)}")
            print(
                "Active window starts with summary:",
                bool(stored.events and stored.events[0].is_summary_event()),
            )
            print(
                "Session Memory state present:",
                "_trpc_agent:summary" in stored.state,
            )
            print("Event IDs:", [event.id for event in stored.events])
            print("Historical IDs:", [event.id for event in stored.historical_events])
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
