#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Multi-agent example demonstrating start_from_last_agent feature.

This example shows how start_from_last_agent=True allows follow-up questions
to be handled by the last active sub-agent instead of routing back through
the coordinator.
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_multi_agent_demo():
    """Run the multi-agent demo with start_from_last_agent enabled."""

    app_name = "multi_agent_start_from_last_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Create run config with start_from_last_agent enabled
    run_config = RunConfig(start_from_last_agent=True)

    # Demo conversation showing the feature:
    # 1. First message goes to coordinator, which routes to sales
    # 2. Second message (follow-up) goes directly to sales, not back to coordinator
    demo_queries = [
        "I'm interested in your smart speakers. What do you have?",
        "What about the display products?",  # Follow-up goes directly to sales
        "Are there any discounts available?",  # Another follow-up to sales
    ]

    print("=" * 60)
    print("Multi-Agent Demo: start_from_last_agent=True")
    print("=" * 60)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows how follow-up questions stay with the")
    print("last active agent instead of routing back to the coordinator.\n")
    print("-" * 60)

    for i, query in enumerate(demo_queries, 1):
        print(f"\n[Turn {i}] User: {query}")
        print("-" * 40)

        user_content = Content(parts=[Part.from_text(text=query)])

        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
                run_config=run_config,
        ):
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"[{event.author}] Tool: {part.function_call.name}({part.function_call.args})")
                elif part.function_response:
                    print(f"[{event.author}] Result: {part.function_response.response}...")
                elif part.text:
                    print(f"[{event.author}] {part.text}")

    print("\n" + "=" * 60)
    print("Demo completed!")
    print("=" * 60)


if __name__ == "__main__":
    print("Multi-Agent Start From Last Agent Example")
    print("Shows how follow-up queries stay with the last active sub-agent\n")
    asyncio.run(run_multi_agent_demo())
