#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Remote A2A Agent Client (Standard Protocol over HTTP)

This example demonstrates how to use TrpcRemoteA2aAgent to connect to a
remote A2A service (standard protocol) over standard HTTP and interact with it
using the Runner interface. The standard protocol uses artifact-first streaming
and unprefixed metadata keys.
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a import TrpcRemoteA2aAgent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

AGENT_BASE_URL = "http://127.0.0.1:18081"


async def run_remote_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
) -> None:
    """Run remote agent with a single query and handle events.

    Args:
        runner: The runner instance
        user_id: User identifier
        session_id: Session identifier
        query: User query text
    """
    user_content = Content(parts=[Part.from_text(text=query)])

    run_config = RunConfig(agent_run_config={
        "metadata": {
            "user_id": user_id,
        },
    })

    print("Remote Agent: ", end="", flush=True)
    try:
        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
                run_config=run_config,
        ):
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n[Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"[Tool Result: {part.function_response.response}]")

    except Exception as e:
        print(f"\nError: {e}")

    print()


def create_runner(
    app_name: str,
    session_service: InMemorySessionService,
    remote_agent: TrpcRemoteA2aAgent,
) -> Runner:
    """Create a new runner instance with remote agent.

    Args:
        app_name: Application name
        session_service: Session service instance
        remote_agent: Remote A2A agent instance

    Returns:
        A new Runner instance
    """
    return Runner(app_name=app_name, agent=remote_agent, session_service=session_service)


async def run_demo(remote_agent: TrpcRemoteA2aAgent) -> None:
    """Run a multi-turn conversation demo."""

    print("=" * 60)
    print("A2A Remote Agent Demo (Standard Protocol over HTTP)")
    print("=" * 60)
    print()

    app_name = "a2a_standard_agent_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    session_service = InMemorySessionService()

    queries = [
        "Hello, my name is Alice.",
        "What's the weather in Beijing?",
        "What's my name and what did I just ask?",
    ]

    for i, query in enumerate(queries, 1):
        print(f"=== Turn {i}/{len(queries)} ===")
        print(f"Session ID: {session_id[:8]}...")
        print(f"User Query: {query}")
        print()

        runner = create_runner(app_name, session_service, remote_agent)
        await run_remote_agent(runner, user_id, session_id, query)

        print()

    print("=" * 60)
    print("Demo completed!")
    print("=" * 60)


async def main():
    """Main function, creates remote agent via HTTP and runs demo."""
    print("Remote A2A Agent Example")
    print("Note: Ensure the A2A server is running (python run_server.py)")
    print()

    remote_agent = TrpcRemoteA2aAgent(
        name="weather_agent",
        agent_base_url=AGENT_BASE_URL,
        description="Professional weather query assistant",
    )
    await remote_agent.initialize()

    await run_demo(remote_agent)


if __name__ == "__main__":
    asyncio.run(main())
