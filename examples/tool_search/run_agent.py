#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run an Agent with on-demand tool discovery."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import ToolSearchOptions
from trpc_agent_sdk.tools import ToolSearchInvocationMode
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv(Path(__file__).with_name(".env"))


async def main() -> None:
    """Run one request while preserving discovered tools in the session."""
    user_id = "tool_search_user"
    app_name = "tool_search_agent_demo"
    options = ToolSearchOptions(
        invocation_mode=ToolSearchInvocationMode.NATIVE,
        catalog_in_description=False,
    )
    from agent.agent import create_agent
    root_agent = create_agent(options)
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    # Demo query list
    demo_queries = [
        "What is the three-day weather forecast for Shenzhen?",
    ]

    for query in demo_queries:
        # Use a new session for each query
        current_session_id = str(uuid.uuid4())

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            # Check if event.content exists
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                # Skip the reasoning part; the output is already generated when partial=True
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # Uncomment to get the full text output of the LLM
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(main())
