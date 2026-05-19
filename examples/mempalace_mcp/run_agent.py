#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the MemPalace MCP demo.

This example talks to the official MemPalace MCP server (`mempalace mcp`)
through tRPC-Agent's stdio MCP toolset. The demo exercises a handful of
representative tools: status, drawer write, semantic search, KG add/query, and
agent diary.
"""

import asyncio
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

sys.path.append(str(Path(__file__).parent))


def _truncate(text: str, max_length: int = 320) -> str:
    text = str(text)
    return text if len(text) <= max_length else text[:max_length] + "..."


async def run_mempalace_mcp_agent() -> None:
    """Run the MemPalace MCP demo."""

    from agent.agent import root_agent

    app_name = "mempalace_mcp_demo"
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # Each query is run in its own session so we can also confirm cross-session
    # recall works through the MemPalace MCP server.
    demo_queries = [
        # Overview
        "Give me a one-line overview of my MemPalace.",
        # Write a verbatim drawer
        "Please remember this for me: I prefer working in the morning. "
        "Store it under wing 'preferences', room 'work_habits'.",
        # Search the drawer back
        "What do you know about my work habits?",
        # Knowledge graph: add a fact
        "Record a fact: Alice prefers blue.",
        # Knowledge graph: query the fact
        "What facts do you know about Alice?",
        # Agent diary: write
        "Write a diary entry as agent 'mempalace_assistant': "
        "Today I helped the user file two memories about their preferences.",
        # Agent diary: read
        "Read back the latest diary entries for agent 'mempalace_assistant'.",
    ]

    try:
        for query in demo_queries:
            session_id = str(uuid.uuid4())
            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

            print(f"\n🆔 Session: {session_id[:8]}")
            print(f"📝 User: {query}")
            print("🤖 Assistant: ", end="", flush=True)

            user_content = Content(parts=[Part.from_text(text=query)])
            async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
                if not event.content or not event.content.parts:
                    continue

                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                    continue

                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.function_call:
                        print(f"\n🔧 [MCP tool: {part.function_call.name}({part.function_call.args})]")
                    elif part.function_response:
                        print(f"📊 [Result: {_truncate(part.function_response.response)}]")

            print("\n" + "-" * 60)
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(run_mempalace_mcp_agent())
