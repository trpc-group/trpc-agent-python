#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Remote A2A Agent Cancel Demo

This example demonstrates how to cancel a running A2A agent via the
TrpcRemoteA2aAgent + Runner interface. It covers two scenarios:

1. Cancel during LLM streaming - triggers cancellation after receiving
   a certain number of streaming events.
2. Cancel during tool execution - triggers cancellation when a tool call
   is detected, while the tool is still executing on the server.

After each cancellation, a follow-up query verifies that session state
is maintained and the agent can continue to respond.
"""

import asyncio
import uuid
from typing import Awaitable
from typing import Callable
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a import TrpcRemoteA2aAgent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

AGENT_BASE_URL = "http://127.0.0.1:18082"
CANCEL_TIMEOUT = 3.0


async def run_remote_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
    tool_call_callback: Optional[Callable[[], Awaitable[None]]] = None,
    event_count_callback: Optional[Callable[[int], Awaitable[None]]] = None,
) -> None:
    """Run remote agent with a single query and handle events.

    Args:
        runner: The runner instance
        user_id: User identifier
        session_id: Session identifier
        query: User query text
        tool_call_callback: Optional async callback triggered when tool call is detected
        event_count_callback: Optional async callback triggered for each event with count
    """
    user_content = Content(parts=[Part.from_text(text=query)])

    run_config = RunConfig(agent_run_config={
        "metadata": {
            "user_id": user_id,
        },
    })

    print("🤖 Remote Agent: ", end="", flush=True)
    event_count = 0
    try:
        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
                run_config=run_config,
        ):
            event_count += 1
            if event_count_callback:
                await event_count_callback(event_count)

            if isinstance(event, AgentCancelledEvent):
                print(f"\n❌ Run was cancelled: {event.error_message}")
                break

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
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                    if tool_call_callback:
                        await tool_call_callback()
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")

    except Exception as e:
        print(f"\n⚠️ Error: {e}")

    print()


def create_runner(
    app_name: str,
    session_service: InMemorySessionService,
    remote_agent: TrpcRemoteA2aAgent,
) -> Runner:
    """Create a new runner instance with remote agent."""
    return Runner(app_name=app_name, agent=remote_agent, session_service=session_service)


async def scenario_1_cancel_during_streaming(remote_agent: TrpcRemoteA2aAgent) -> None:
    """Scenario 1: Cancel while the remote agent is streaming its LLM response.

    Triggers cancellation after receiving the first 10 streaming events.
    Then sends a follow-up query to verify session state is maintained.
    """
    print("📋 Scenario 1: Cancel During LLM Streaming (Remote A2A)")
    print("-" * 80)

    app_name = "a2a_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()

    query1 = "Introduce yourself in detail, what can you do as a weather assistant."
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User Query 1: {query1}")
    print()

    event_threshold_reached = asyncio.Event()

    async def on_event_count(count: int) -> None:
        if count == 10:
            print(f"\n⏳ [Received {count} events, triggering cancellation...]")
            event_threshold_reached.set()

    runner1 = create_runner(app_name, session_service, remote_agent)

    async def run_query1() -> None:
        await run_remote_agent(runner1, user_id, session_id, query1, event_count_callback=on_event_count)

    task = asyncio.create_task(run_query1())

    print("⏳ Waiting for first 10 events...")
    await event_threshold_reached.wait()

    runner2 = create_runner(app_name, session_service, remote_agent)
    print("\n⏸️  Requesting cancellation after 10 events...")
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id, timeout=CANCEL_TIMEOUT)
    print(f"✓ Cancellation requested: {success}")

    await task

    print()
    print("💡 Result: The partial response was saved to session with cancellation message")
    print()

    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    runner3 = create_runner(app_name, session_service, remote_agent)
    await run_remote_agent(runner3, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


async def scenario_2_cancel_during_tool_execution(remote_agent: TrpcRemoteA2aAgent) -> None:
    """Scenario 2: Cancel while the remote agent is executing a tool.

    Triggers cancellation when a tool call event is detected. The tool has
    a 2-second simulated delay, giving time for the cancel request to arrive
    while execution is in progress.
    """
    print("📋 Scenario 2: Cancel During Tool Execution (Remote A2A)")
    print("-" * 80)

    app_name = "a2a_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()

    query1 = "What's the current weather in Shanghai and Beijing?"
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User Query 1: {query1}")
    print()

    tool_call_detected = asyncio.Event()

    async def on_tool_call() -> None:
        print("⏳ [Tool call detected...]")
        tool_call_detected.set()

    runner1 = create_runner(app_name, session_service, remote_agent)

    async def run_query1() -> None:
        await run_remote_agent(runner1, user_id, session_id, query1, tool_call_callback=on_tool_call)

    task = asyncio.create_task(run_query1())

    print("⏳ Waiting for tool call to be detected...")
    await tool_call_detected.wait()

    runner2 = create_runner(app_name, session_service, remote_agent)
    print("\n⏸️  Tool call detected! Requesting cancellation during tool execution...")
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id, timeout=CANCEL_TIMEOUT)
    print(f"✓ Cancellation requested: {success}")

    await task

    print()
    print("💡 Result: Incomplete function calls were cleaned up from session")
    print()

    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    runner3 = create_runner(app_name, session_service, remote_agent)
    await run_remote_agent(runner3, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


async def main():
    """Main function: create remote agent and run cancel demo scenarios."""
    print("Remote A2A Agent Cancel Example")
    print("Note: Ensure the A2A server is running (python run_server.py)")
    print()

    remote_agent = TrpcRemoteA2aAgent(
        name="weather_agent",
        agent_base_url=AGENT_BASE_URL,
        description="Professional weather query assistant with cancel support",
    )
    await remote_agent.initialize()

    print("=" * 80)
    print("🎯 A2A Agent Cancellation Demo")
    print("=" * 80)
    print()

    await scenario_1_cancel_during_streaming(remote_agent)

    await scenario_2_cancel_during_tool_execution(remote_agent)

    print()
    print("=" * 80)
    print("✅ Demo completed!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
