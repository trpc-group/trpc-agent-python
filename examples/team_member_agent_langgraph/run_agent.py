#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TeamAgent with LangGraphAgent Member example.

This example shows how to use LangGraphAgent as a member of TeamAgent:
- Leader coordinates tasks
- LangGraph member executes calculations
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_team_demo():
    """Run the team demo."""

    app_name = "langgraph_member_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    print("=" * 60)
    print("TeamAgent with LangGraphAgent Member Demo")
    print("=" * 60)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows how LangGraphAgent can be used as a")
    print("team member for math calculations.\n")
    print("-" * 60)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    # Test queries
    queries = [
        "Calculate 15 * 23 for me",
        "What's 100 divided by 4?",
    ]

    try:
        for turn, query in enumerate(queries, 1):
            print(f"\n[Turn {turn}] User: {query}")
            print("-" * 40)

            user_message = Content(parts=[Part.from_text(text=query)])
            author = None

            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=user_message,
            ):
                if event.content and event.content.parts:
                    if not event.partial:
                        for part in event.content.parts:
                            if part.function_call:
                                print(f"\n[{event.author}] Tool: {part.function_call.name}, "
                                      f"Args: {part.function_call.args}")
                                author = event.author
                            elif part.function_response:
                                author = event.author
                                print(f"\n[{event.author}] Tool Response: {part.function_response.response}")
                    else:
                        for part in event.content.parts:
                            if part.text:
                                if author != event.author:
                                    author = event.author
                                    print(f"\n[{author}] ", end="")
                                print(f"{part.text}", end="", flush=True)

            print("\n")
    finally:
        await runner.close()

    print("=" * 60)
    print("Demo completed!")
    print("=" * 60)


if __name__ == "__main__":
    print("TeamAgent with LangGraphAgent Member Example")
    print("Demonstrates: Leader -> LangGraph Member (calculator_expert)")
    print()
    asyncio.run(run_team_demo())
