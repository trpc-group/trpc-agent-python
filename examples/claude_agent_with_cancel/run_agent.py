# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
ClaudeAgent Cancellation Demo

This example demonstrates the agent cancellation feature for ClaudeAgent,
showing two realistic scenarios:
1. Cancel during streaming (using sleep)
2. Cancel during tool execution (using event synchronization)

Each scenario contains 2 queries:
- First query: Ask about weather
- Second query: Ask "what happens?" to see if session state is maintained
"""

import asyncio
import uuid
from typing import Awaitable
from typing import Callable
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
    tool_call_callback: Optional[Callable[[], Awaitable[None]]] = None,
    event_count_callback: Optional[Callable[[int], Awaitable[None]]] = None,
) -> None:
    """Run agent with a single query and handle events.

    Args:
        runner: The runner instance
        user_id: User identifier
        session_id: Session identifier
        query: User query text
        tool_call_callback: Optional async callback triggered when tool call is detected
        event_count_callback: Optional async callback triggered for each event with count
    """
    user_content = Content(parts=[Part.from_text(text=query)])

    print("🤖 Assistant: ", end="", flush=True)
    event_count = 0
    try:
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            # Increment event count and trigger callback if provided
            event_count += 1
            if event_count_callback:
                await event_count_callback(event_count)
            # Check for cancellation using AgentCancelledEvent
            if isinstance(event, AgentCancelledEvent):
                print(f"\n❌ Run was cancelled: {event.error_message}")
                break

            if not event.content or not event.content.parts:
                continue

            # Print streaming text
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # Print tool calls and responses
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Tool Call: {part.function_call.name}({part.function_call.args})]")
                    if tool_call_callback:
                        await tool_call_callback()
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")

            # # Print final text response
            # for part in event.content.parts:
            #     if part.text and not event.partial:
            #         print(part.text, end="", flush=True)

    except Exception as e:
        print(f"\n⚠️ Error: {e}")

    print()  # New line after response


def create_runner(app_name: str, session_service: InMemorySessionService, agent) -> Runner:
    """Create a new runner instance.

    Args:
        app_name: Application name
        session_service: Session service instance
        agent: The ClaudeAgent instance

    Returns:
        A new Runner instance
    """
    return Runner(app_name=app_name, agent=agent, session_service=session_service)


async def scenario_1_cancel_during_streaming(agent) -> None:
    """Scenario 1: Cancel while Claude is streaming response.

    This scenario uses asyncio.Event to trigger cancellation after receiving
    the first 10 events during streaming.
    Each query creates a new runner instance to avoid state issues.

    Queries:
    1. Ask for introduction (will be cancelled after 10 events)
    2. Ask "what happens?" to verify session state
    """

    print("📋 Scenario 1: Cancel During Streaming")
    print("-" * 80)

    # Common settings for this scenario
    app_name = "claude_agent_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Create session service (shared across queries)
    session_service = InMemorySessionService()

    # Query 1: Ask for introduction (will be cancelled)
    query1 = "Introduce yourself, what can you do."
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User Query 1: {query1}")
    print()

    # Event to signal when we've received 10 events
    event_threshold_reached = asyncio.Event()

    # Create callback for event counting
    async def on_event_count(count: int) -> None:
        if count == 10:
            print(f"\n⏳ [Received {count} events, triggering cancellation...]")
            event_threshold_reached.set()

    # Create new runner for query 1
    runner1 = create_runner(app_name, session_service, agent)

    # Create background task to run agent
    async def run_query1() -> None:
        await run_agent(runner1, user_id, session_id, query1, event_count_callback=on_event_count)

    # Start agent in background
    task = asyncio.create_task(run_query1())

    # Wait for 10 events to be received
    print("⏳ Waiting for first 10 events...")
    await event_threshold_reached.wait()

    # Cancel the run after receiving 10 events
    runner2 = create_runner(app_name, session_service, agent)
    print("\n⏸️  Requesting cancellation after 10 events...")
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id)
    print(f"✓ Cancellation requested: {success}")

    # Wait for task to complete
    await task

    print()
    print("💡 Result: The partial response was saved to session with cancellation message")
    print()

    # Query 2: Ask "what happens?" to verify session state
    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    # Create new runner for query 2
    runner2 = create_runner(app_name, session_service, agent)
    await run_agent(runner2, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


async def scenario_2_cancel_during_tool_execution(agent) -> None:
    """Scenario 2: Cancel while agent is executing tools.

    This scenario uses asyncio.Event to synchronize cancellation exactly when
    tool execution starts. Each query creates a new runner instance to
    avoid state issues.

    Queries:
    1. Ask for weather (will be cancelled during tool execution)
    2. Ask "what happens?" to verify session state
    """

    print("📋 Scenario 2: Cancel During Tool Execution")
    print("-" * 80)

    # Common settings for this scenario
    app_name = "claude_agent_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Create session service (shared across queries)
    session_service = InMemorySessionService()

    # Query 1: Ask for weather (will be cancelled)
    query1 = "What's the current weather in Shanghai and Beijing?"
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User Query 1: {query1}")
    print()

    # Event to signal when function_call is received
    tool_call_detected = asyncio.Event()

    # Create callback for tool call detection
    async def on_tool_call() -> None:
        print("⏳ [Tool call detected...]")
        tool_call_detected.set()

    # Create new runner for query 1
    runner1 = create_runner(app_name, session_service, agent)

    # Create background task to run agent
    async def run_query1() -> None:
        await run_agent(runner1, user_id, session_id, query1, tool_call_callback=on_tool_call)

    # Start agent in background
    task = asyncio.create_task(run_query1())

    # Wait for tool call to be detected
    print("⏳ Waiting for tool call to be detected...")
    await tool_call_detected.wait()

    # Now cancel immediately after tool call is detected (tool is still executing)
    runner2 = create_runner(app_name, session_service, agent)
    print("\n⏸️  Tool call detected! Requesting cancellation during tool execution...")
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id)
    print(f"✓ Cancellation requested: {success}")

    # Wait for task to complete
    await task

    print()
    print("💡 Result: Incomplete function calls were cleaned up from session")
    print()

    # Query 2: Ask "what happens?" to verify session state
    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    # Create new runner for query 2
    runner2 = create_runner(app_name, session_service, agent)
    await run_agent(runner2, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


async def run_with_cancellation_demo() -> None:
    """Demonstrate ClaudeAgent cancellation in realistic scenarios."""

    print("=" * 80)
    print("🎯 ClaudeAgent Cancellation Demo")
    print("=" * 80)
    print()

    # Import agent and initialize Claude environment
    from agent.agent import root_agent, _create_model

    # Setup Claude environment with proxy server
    model = _create_model()
    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8082, claude_models={"all": model})
    root_agent.initialize()

    try:
        # Scenario 1: Cancel during streaming
        await scenario_1_cancel_during_streaming(root_agent)

        # Scenario 2: Cancel during tool execution
        await scenario_2_cancel_during_tool_execution(root_agent)

        print()
        print("=" * 80)
        print("✅ Demo completed!")
        print("=" * 80)

    finally:
        # Cleanup
        root_agent.destroy()
        destroy_claude_env()
        print("🧹 Claude environment cleaned up")


if __name__ == "__main__":
    asyncio.run(run_with_cancellation_demo())
