#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Run native Session compaction over the standard SqlSessionService."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from trpc_agent_sdk.sessions.compact import AdvancedCompactConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


def sql_url() -> str:
    """Build the MySQL connection URL from environment variables."""
    db_user = os.environ.get("MYSQL_USER", "root")
    db_password = os.environ.get("MYSQL_PASSWORD", "")
    db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    db_port = os.environ.get("MYSQL_PORT", "3306")
    db_name = os.environ.get("MYSQL_DB", "trpc_agent_session")
    return (
        f"mysql+pymysql://{db_user}:{db_password}@"
        f"{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    )


def create_compact_config() -> AdvancedCompactConfig:
    """Configure only the settings needed to demonstrate one compaction."""
    return AdvancedCompactConfig(
        model_context_window_tokens=4096,
        max_output_tokens=256,
        token_warning_ratio=0.25,
        token_autocompact_ratio=0.30,
        token_blocking_ratio=0.95,
        session_memory_initial_tokens=500,
        session_memory_update_tokens=500,
        autocompact_keep_recent_contents=2,
    )


async def main() -> None:
    """Attach Session Compact to SqlSessionService and run the demo."""
    app_name = "session-service-advanced-memory-sql"
    user_id = "demo-user"
    session_id = os.getenv("SESSION_ID", "simple-demo")
    from agent.agent import create_agent

    agent = create_agent()
    compact_config = create_compact_config()
    session_config = SessionServiceConfig(store_historical_events=True)
    session_service = SqlSessionService(
        db_url=sql_url(),
        is_async=False,
        session_config=session_config,
        session_compact_config=compact_config,
    )
    runner = Runner(
        app_name=app_name,
        agent=agent,
        session_service=session_service,
    )
    try:
        for prompt in (
            "Generate a report about SQL session persistence.",
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
