#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TeamAgent with ClaudeAgent Member example.

This example shows how to use ClaudeAgent as a member of TeamAgent:
- Leader coordinates tasks
- Claude member executes weather queries
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

    app_name = "claude_member_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    print("=" * 60)
    print("TeamAgent with ClaudeAgent Member Demo")
    print("=" * 60)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows how ClaudeAgent can be used as a")
    print("team member for weather queries.\n")
    print("-" * 60)

    # Setup Claude environment first
    from agent.agent import setup_environment, create_team, cleanup_environment
    setup_environment()

    # Create team and runner
    team, claude_agent = create_team()
    session_service = InMemorySessionService()

    # Test queries
    queries = [
        "What's the weather in Beijing?",
        "How about Shanghai?",
    ]

    try:
        for turn, query in enumerate(queries, 1):
            runner = Runner(app_name=app_name, agent=team, session_service=session_service)
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

            await runner.close()
    finally:
        claude_agent.destroy()
        cleanup_environment()
        print("Cleaned up Claude environment")

    print("=" * 60)
    print("Demo completed!")
    print("=" * 60)


if __name__ == "__main__":
    print("TeamAgent with ClaudeAgent Member Example")
    print("Demonstrates: Leader -> Claude Member (weather_expert)")
    print()
    asyncio.run(run_team_demo())
