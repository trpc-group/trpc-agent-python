#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Content Team example demonstrating TeamAgent coordinate mode.

This example shows how TeamAgent coordinates tasks between members:
- Leader delegates to researcher for information gathering
- Writer creates content based on research results
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
    """Run the content team demo with 2-turn conversation."""

    app_name = "content_team_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Demo conversation - 2 turns to demonstrate multi-turn support
    demo_queries = [
        "Please write a short article about renewable energy",
        "Please help me add some content about AI",
    ]

    print("=" * 60)
    print("Content Team Demo - Coordinate Mode")
    print("=" * 60)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows how TeamAgent coordinates tasks between")
    print("researcher and writer members.\n")
    print("-" * 60)

    for i, query in enumerate(demo_queries, 1):
        print(f"\n[Turn {i}] User: {query}")
        print("-" * 40)

        user_content = Content(parts=[Part.from_text(text=query)])
        author = None

        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
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
                            print(f"\n[{event.author}] Tool Response: {part.function_response}")
                else:
                    for part in event.content.parts:
                        if part.text:
                            if author != event.author:
                                author = event.author
                                print(f"\n[{author}] ", end="")
                            print(f"{part.text}", end="", flush=True)

        print("\n")

    print("=" * 60)
    print("Demo completed!")
    print("=" * 60)

    await runner.close()


if __name__ == "__main__":
    print("Content Team Example")
    print("Demonstrates coordinate mode: Leader -> Researcher -> Writer")
    print()
    asyncio.run(run_team_demo())
