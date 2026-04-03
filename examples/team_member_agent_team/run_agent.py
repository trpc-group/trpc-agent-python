#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Hierarchical Team example demonstrating TeamAgent as member.

This example shows nested TeamAgent structure:
- project_manager (TeamAgent) delegates to:
  - dev_team (TeamAgent) which further delegates to backend_dev and frontend_dev
  - doc_writer (LlmAgent) for documentation
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_hierarchical_team_demo():
    """Run the hierarchical team demo with nested TeamAgent."""

    app_name = "hierarchical_team_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Demo conversation - demonstrates nested team delegation
    demo_queries = [
        "Please implement a user authentication feature with login UI and API",
    ]

    print("=" * 70)
    print("Hierarchical Team Demo - TeamAgent as Member")
    print("=" * 70)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows nested TeamAgent structure:")
    print("  project_manager (TeamAgent)")
    print("    -> dev_team (TeamAgent as member)")
    print("       -> backend_dev (LlmAgent)")
    print("       -> frontend_dev (LlmAgent)")
    print("    -> doc_writer (LlmAgent)")
    print("\n" + "-" * 70)

    for i, query in enumerate(demo_queries, 1):
        print(f"\n[Turn {i}] User: {query}")
        print("-" * 50)

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
                            # Truncate long responses for readability
                            response_str = str(part.function_response)
                            if len(response_str) > 100:
                                response_str = response_str[:100] + "..."
                            print(f"\n[{event.author}] Tool Response: {response_str}")
                else:
                    for part in event.content.parts:
                        if part.text:
                            if author != event.author:
                                author = event.author
                                print(f"\n[{author}] ", end="")
                            print(f"{part.text}", end="", flush=True)

        print("\n")

    print("=" * 70)
    print("Demo completed!")
    print("=" * 70)

    await runner.close()


if __name__ == "__main__":
    print("Hierarchical Team Example")
    print("Demonstrates TeamAgent as member of another TeamAgent")
    print("Structure: project_manager -> dev_team -> [backend_dev, frontend_dev]")
    print("                           -> doc_writer")
    print()
    asyncio.run(run_hierarchical_team_demo())
