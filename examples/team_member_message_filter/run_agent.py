#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Member Message Filter example demonstrating TeamAgent message filtering.

This example shows how member_message_filter controls message aggregation:
- keep_all_member_message: Keep all member messages (default)
- keep_last_member_message: Keep only last member message
- Custom filter: Implement custom filtering logic
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_demo():
    """Run demo for the team with custom message filter."""

    app_name = "filter_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    print("=" * 70)
    print("Member Message Filter Demo")
    print("=" * 70)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows how member_message_filter controls how")
    print("member messages are aggregated for the leader.\n")
    print("-" * 70)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    query = "Please analyze this year's regional sales performance and provide improvement recommendations"

    print(f"\nUser: {query}")
    print("-" * 50)

    user_message = Content(parts=[Part.from_text(text=query)])
    author = None

    try:
        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_message,
        ):
            if event.content and event.content.parts:
                if not event.partial:
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n[{event.author}] Tool call: {part.function_call.name}")
                            author = event.author
                        elif part.function_response:
                            author = event.author
                            # Simplified display of tool response
                            response_preview = str(part.function_response.response)[:80]
                            print(f"[{event.author}] Tool response: {response_preview}...")
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

    print("=" * 70)
    print("Demo completed!")
    print("=" * 70)


if __name__ == "__main__":
    print("Member Message Filter Example")
    print("Demonstrates the effects of different member_message_filter filters")
    print()
    asyncio.run(run_demo())
