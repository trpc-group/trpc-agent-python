#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
HITL Team example demonstrating Human-in-the-Loop with TeamAgent.

This example shows how TeamAgent handles human intervention:
- Leader triggers HITL via LongRunningFunctionTool
- System pauses and yields LongRunningEvent
- User provides approval (simulated)
- Team resumes and completes the task
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

load_dotenv()


@dataclass
class InvocationParams:
    """Parameters for running an invocation."""
    user_id: str
    session_id: str
    agent: any
    session_service: InMemorySessionService
    app_name: str


async def run_invocation(
    params: InvocationParams,
    content: Content,
) -> Optional[LongRunningEvent]:
    """Run an invocation with a fresh runner instance.

    Args:
        params: Invocation parameters
        content: The content to send to the agent

    Returns:
        LongRunningEvent if one is encountered, None otherwise
    """
    runner = Runner(app_name=params.app_name, agent=params.agent, session_service=params.session_service)

    captured_hitl_event = None

    try:
        async for event in runner.run_async(
                user_id=params.user_id,
                session_id=params.session_id,
                new_message=content,
        ):
            if isinstance(event, LongRunningEvent):
                captured_hitl_event = event
                print(f"\n{'=' * 40}")
                print("HITL TRIGGERED!")
                print(f"Function: {event.function_call.name}")
                print(f"Args: {event.function_call.args}")
                print(f"Response: {event.function_response.response}")
                print(f"{'=' * 40}")
                print("Waiting for human intervention...")
            elif event.content and event.content.parts and event.author != "user":
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n[{event.author}] Tool: {part.function_call.name}")
                        elif part.function_response:
                            print(f"[{event.author}] Tool Result: {str(part.function_response.response)[:80]}...")
    finally:
        await runner.close()

    return captured_hitl_event


async def run_hitl_demo():
    """Run the HITL demo."""

    app_name = "hitl_team_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    print("=" * 60)
    print("HITL Team Demo - Human-in-the-Loop")
    print("=" * 60)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows how TeamAgent handles human intervention")
    print("for approval workflows.\n")
    print("-" * 60)

    from agent.agent import root_agent
    session_service = InMemorySessionService()

    params = InvocationParams(
        user_id=user_id,
        session_id=session_id,
        agent=root_agent,
        session_service=session_service,
        app_name=app_name,
    )

    # Request that triggers HITL
    query = "Please help me search for AI-related information, then 'publish' a report"
    print(f"\n[User] {query}")
    print("-" * 40)
    print("Assistant: ", end="", flush=True)

    user_message = Content(parts=[Part.from_text(text=query)])

    # First invocation - will trigger HITL
    hitl_event = await run_invocation(params, user_message)

    # Handle HITL resume
    if hitl_event:
        print("\n" + "-" * 40)
        print("Human intervention simulation...")
        await asyncio.sleep(1)

        # Build approval response
        response_data = dict(hitl_event.function_response.response)
        response_data["status"] = "approved"
        response_data["approved_by"] = "admin"
        response_data["timestamp"] = time.time()

        print(f"Human provides approval: {response_data}")
        print("-" * 40)
        print("Resuming team execution...")
        print("Assistant: ", end="", flush=True)

        # Build resume content with FunctionResponse
        resume_response = FunctionResponse(
            id=hitl_event.function_response.id,
            name=hitl_event.function_response.name,
            response=response_data,
        )
        resume_content = Content(role="user", parts=[Part(function_response=resume_response)])

        # Second invocation - resume with approval
        await run_invocation(params, resume_content)

        print("\n" + "=" * 60)
        print("HITL Flow Completed!")
        print("=" * 60)

    print()


if __name__ == "__main__":
    print("HITL Team Example")
    print("Demonstrates Human-in-the-Loop with TeamAgent")
    print()
    asyncio.run(run_hitl_demo())
