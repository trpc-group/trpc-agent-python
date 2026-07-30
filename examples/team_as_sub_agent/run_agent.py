#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TeamAgent as Sub-Agent example demonstrating transfer_to_agent mechanism.

This example shows how a TeamAgent (as a sub_agent) can transfer control
to parent agent or sibling agents after completing tasks.

Architecture:
    coordinator (Root LlmAgent)
    ├── finance_team (TeamAgent)
    │   ├── analyst (LlmAgent member)
    │   └── auditor (LlmAgent member)
    └── report_agent (LlmAgent)
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_test_scenario(runner: Runner, scenario_name: str, query: str, user_id: str):
    """Run a single test scenario.

    Args:
        runner: The Runner instance
        scenario_name: Name of the test scenario
        query: User query to send
        user_id: User ID for the session
    """
    session_id = str(uuid.uuid4())

    print(f"\n{'=' * 60}")
    print(f"Test Scenario: {scenario_name}")
    print(f"{'=' * 60}")
    print(f"Session ID: {session_id[:8]}...")
    print(f"Query: {query}")
    print("-" * 60)

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
                        print(f"\n[{event.author}] Tool: {part.function_call.name}")
                        print(f"  Args: {part.function_call.args}")
                        author = event.author
                    elif part.function_response:
                        author = event.author
                        response_preview = str(part.function_response.response)[:100]
                        print(f"\n[{event.author}] Tool Response: {response_preview}...")
            else:
                for part in event.content.parts:
                    if part.text:
                        if author != event.author:
                            author = event.author
                            print(f"\n[{author}] ", end="")
                        print(f"{part.text}", end="", flush=True)

    print("\n" + "=" * 60)


async def run_demo():
    """Run the TeamAgent as sub_agent demo with 2 test scenarios."""

    app_name = "team_as_sub_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    print("=" * 60)
    print("TeamAgent as Sub-Agent Demo")
    print("=" * 60)
    print("\nThis demo shows how TeamAgent works as a sub_agent and")
    print("demonstrates transfer_to_agent mechanism:\n")
    print("Architecture:")
    print("  coordinator (Root)")
    print("  ├── finance_team (TeamAgent)")
    print("  │   ├── analyst (can transfer)")
    print("  │   └── auditor")
    print("  └── report_agent (sibling)")
    print("\n" + "=" * 60)

    # Test 1: Transfer to parent agent (coordinator)
    await run_test_scenario(
        runner=runner,
        scenario_name="Test 1: Transfer to Parent Agent",
        query="Please analyze Q4 financial data and then transfer to coordinator to summarize.",
        user_id=user_id,
    )

    # Test 2: Transfer to sibling agent (report_agent)
    await run_test_scenario(
        runner=runner,
        scenario_name="Test 2: Transfer to Sibling Agent",
        query="Please analyze Q4 financial data and then transfer to report_agent to generate a report",
        user_id=user_id,
    )

    print(f"\n{'=' * 60}")
    print("Demo completed!")
    print(f"{'=' * 60}")

    await runner.close()


if __name__ == "__main__":
    print("TeamAgent as Sub-Agent Example")
    print("Demonstrates: TeamAgent -> transfer_to_agent -> Parent/Sibling")
    print()
    asyncio.run(run_demo())
