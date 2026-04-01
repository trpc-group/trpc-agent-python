#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Parallel Execution Team example demonstrating TeamAgent parallel_execution mode.

This example shows how TeamAgent executes multiple member delegations in parallel:
- Leader delegates to market_analyst, competitor_analyst, and risk_analyst simultaneously
- All three analysts execute concurrently (via asyncio.gather)
- Results are collected and synthesized by the leader

Key difference from sequential mode:
- Sequential: analyst1 -> analyst2 -> analyst3 (total time = sum of all)
- Parallel: analyst1 | analyst2 | analyst3 (total time = max of all)
"""

import asyncio
import time
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_parallel_team_demo():
    """Run the parallel analysis team demo."""

    app_name = "parallel_analysis_team_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Query designed to trigger parallel delegation to all three analysts
    query = "Please provide a comprehensive analysis of the technology sector, including Google as a key competitor, and assess regulatory risks."

    print("=" * 70)
    print("Parallel Execution Team Demo")
    print("=" * 70)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows how TeamAgent executes multiple delegations in PARALLEL.")
    print("The leader will delegate to 3 analysts simultaneously, and they will")
    print("execute concurrently using asyncio.gather.\n")
    print("-" * 70)

    print(f"\nUser: {query}")
    print("-" * 50)

    user_content = Content(parts=[Part.from_text(text=query)])
    author = None

    start_time = time.time()

    async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
    ):
        if event.content and event.content.parts:
            if not event.partial:
                for part in event.content.parts:
                    if part.function_call:
                        elapsed = time.time() - start_time
                        print(f"\n[{elapsed:.2f}s] [{event.author}] Tool: {part.function_call.name}")
                        if part.function_call.args:
                            print(f"         Args: {part.function_call.args}")
                        author = event.author
                    elif part.function_response:
                        author = event.author
                        elapsed = time.time() - start_time
                        # Truncate long responses for readability
                        response_str = str(part.function_response.response)
                        if len(response_str) > 100:
                            response_str = response_str[:100] + "..."
                        print(f"[{elapsed:.2f}s] [{event.author}] Result: {response_str}")
            else:
                for part in event.content.parts:
                    if part.text:
                        if author != event.author:
                            author = event.author
                            elapsed = time.time() - start_time
                            print(f"\n[{elapsed:.2f}s] [{author}] ", end="")
                        print(f"{part.text}", end="", flush=True)

    total_time = time.time() - start_time
    print("\n")
    print("=" * 70)
    print(f"Demo completed in {total_time:.2f} seconds!")
    print("=" * 70)
    print("\nNote: With parallel_execution=True, the three analyst delegations")
    print("execute concurrently. If this were sequential, total time would be")
    print("significantly longer (sum of all analyst execution times).")

    await runner.close()


if __name__ == "__main__":
    print("Parallel Execution Team Example")
    print("Demonstrates parallel_execution=True: Leader -> [analyst1 | analyst2 | analyst3]")
    print()
    asyncio.run(run_parallel_team_demo())
