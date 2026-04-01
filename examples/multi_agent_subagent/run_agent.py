#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Sub Agents Demo - Intelligent Routing System

Demonstrate hierarchical Agent structure: Coordinator → Specialist Agent
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_sub_agent():
    """Run the Sub-Agent Demo"""

    # Define Application and User Information
    APP_NAME = "subagent_demo"
    USER_ID = "demo_customer"

    print("=" * 40)
    print("Sub-Agents Demo - Intelligent Routing")
    print("=" * 40)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    test_scenarios = [
        {
            "title": "Technical Support",
            "query": "My speaker stopped working. Can you help?",
            "session_id": str(uuid.uuid4()),
        },
        {
            "title": "Sales Inquiry",
            "query": "What security systems do you have?",
            "session_id": str(uuid.uuid4()),
        },
    ]

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\nScenario {i}: {scenario['title']}")
        print(f"Question: {scenario['query']}")
        print("\nProcess:")

        user_message = Content(parts=[Part.from_text(text=scenario["query"])])

        async for event in runner.run_async(user_id=USER_ID,
                                            session_id=scenario["session_id"],
                                            new_message=user_message):
            if event.content and event.content.parts and event.author != "user":
                if not event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(f"[{event.author}] {part.text}")
                        if part.function_call:
                            print(f"🔧 [{event.author}] Invoke Tool: {part.function_call.name} "
                                  f"with arguments: {part.function_call.args}")
                        elif part.function_response:
                            print(f"🔧 [{event.author}] Tool Result: {part.function_response.response}")

        print("-" * 40)


if __name__ == "__main__":
    asyncio.run(run_sub_agent())
