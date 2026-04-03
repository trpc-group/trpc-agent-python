#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


@dataclass
class InvocationParams:
    """Parameters for running an invocation"""

    user_id: str
    session_id: str
    agent: LlmAgent
    session_service: InMemorySessionService
    app_name: str


async def run_invocation(
    params: InvocationParams,
    content: Content,
) -> Optional[LongRunningEvent]:
    """Run an invocation with a fresh runner instance.

    Args:
        params: Invocation parameters containing user_id, session_id, agent, and session_service
        content: The content to send to the agent

    Returns:
        LongRunningEvent if one is encountered, None otherwise
    """
    runner = Runner(app_name=params.app_name, agent=params.agent, session_service=params.session_service)

    captured_long_running_event = None

    try:
        async for event in runner.run_async(user_id=params.user_id, session_id=params.session_id, new_message=content):
            if isinstance(event, LongRunningEvent):
                captured_long_running_event = event
                print(f"\n🔄 [Long-running operation detected]")
                print(f"   Function: {event.function_call.name}")
                print(f"   Response: {event.function_response.response}")
                print("   ⏳ Waiting for human intervention...")
            elif event.content and event.content.parts and event.author != "user":
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [{event.author}][Calling tool: {part.function_call.name}]")
                            print(f"   Args: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [{event.author}][Tool result: {part.function_response.response}]\n")
                        elif part.text:
                            print(f"\n💬 [{event.author}]{part.text}")
    finally:
        await runner.close()

    return captured_long_running_event


async def run_agent():
    """Run the agent with support for long-running events"""

    print("🔧 Long Running Tools Demo with Sub-Agents")
    print("=" * 60)
    print("This demo shows how to handle long-running operations that require human intervention,")
    print("including sub-agents that can raise human-in-the-loop events.")
    print("=" * 60)

    app_name = "agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()

    test_scenarios = [
        {
            "title": "Main Agent - Database Deletion Approval",
            "query":
            "I need approval to delete the production database. The details are: environment=prod, database=user_data, reason=migration",
            "session_id": str(uuid.uuid4()),
            "approval_message": "APPROVED: The database deletion is approved for migration purposes.",
        },
        {
            "title": "Sub-Agent - Critical System Operation",
            "query": "I need to restart the production server 'web-server-01' for maintenance",
            "session_id": str(uuid.uuid4()),
            "approval_message": "APPROVED: Server restart is approved for scheduled maintenance.",
        },
    ]

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n{'=' * 60}")
        print(f"Scenario {i}: {scenario['title']}")
        print(f"{'=' * 60}")

        params = InvocationParams(
            user_id="demo_user",
            session_id=scenario["session_id"],
            agent=root_agent,
            session_service=session_service,
            app_name=app_name,
        )

        print(f"\n📝 Query: {scenario['query']}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=scenario["query"])])

        long_running_event = await run_invocation(params, user_content)

        if long_running_event:
            print("\n👤 Human intervention simulation...")
            await asyncio.sleep(2)

            function_name = long_running_event.function_call.name
            response_data = long_running_event.function_response.response
            print(f"🤖 Assistant: {function_name}: {response_data}")
            if response_data["status"] != "pending_approval":
                print("   ❌ Invalid response status")
                continue

            response_data["status"] = "approved"
            response_data["message"] = scenario["approval_message"]
            response_data["approved_by"] = "admin"
            response_data["timestamp"] = time.time()

            print(f"   Human response: {response_data}")

            resume_function_response = FunctionResponse(
                id=long_running_event.function_response.id,
                name=long_running_event.function_response.name,
                response=response_data,
            )
            resume_content = Content(role="user", parts=[Part(function_response=resume_function_response)])

            print("\n🔄 Resuming agent execution...")

            await run_invocation(params, resume_content)

    print(f"\n{'=' * 60}")
    print("✅ Long Running Tools Demo with Sub-Agents completed!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(run_agent())
