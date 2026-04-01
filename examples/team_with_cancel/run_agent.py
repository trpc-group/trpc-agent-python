# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Content Team with Cancellation Demo

This example demonstrates the team cancellation feature for TeamAgent,
showing three realistic scenarios:
1. Cancel during leader planning (LLM streaming)
2. Cancel during member tool execution (researcher/writer)
3. Resume after cancellation to verify state preservation

Each scenario contains 2 queries:
- First query: Ask to create content (will be cancelled)
- Second query: Ask "what happened?" to see if team state is maintained
"""

import asyncio
import uuid
from typing import Awaitable
from typing import Callable
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
    tool_call_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    event_count_callback: Optional[Callable[[int], Awaitable[None]]] = None,
) -> None:
    """Run agent with a single query and handle events.

    Args:
        runner: The runner instance
        user_id: User identifier
        session_id: Session identifier
        query: User query text
        tool_call_callback: Optional async callback triggered when tool call is detected,
                          receives the tool name as parameter
        event_count_callback: Optional async callback triggered for each event with count
    """
    user_content = Content(parts=[Part.from_text(text=query)])

    print("🤖 Team: ", end="", flush=True)
    current_author = None
    event_count = 0

    try:
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            # Increment event count and trigger callback if provided
            event_count += 1
            if event_count_callback:
                await event_count_callback(event_count)
            # Check for cancellation using AgentCancelledEvent
            if isinstance(event, AgentCancelledEvent):
                print(f"\n❌ Team execution was cancelled: {event.error_message}")
                break

            if not event.content or not event.content.parts:
                continue

            # Print streaming text with author labels
            if event.partial:
                if current_author != event.author:
                    current_author = event.author
                    print(f"\n[{current_author}] ", end="", flush=True)
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # Print tool calls and responses
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [{event.author}] Invoke Tool: {part.function_call.name}({part.function_call.args})")
                    if tool_call_callback:
                        await tool_call_callback(part.function_call.name)
                elif part.function_response:
                    print(f"📊 [{event.author}] Tool Result: {part.function_response.response}")

            # Print final text response
            if current_author != event.author:
                current_author = event.author
                print(f"\n[{current_author}] ", end="", flush=True)
            # for part in event.content.parts:
            #     if part.text and not event.partial:
            #         print(part.text, end="", flush=True)

    except Exception as e:
        print(f"\n⚠️ Error: {e}")

    print()  # New line after response


def create_runner(app_name: str, session_service: InMemorySessionService) -> Runner:
    """Create a new runner instance.

    Args:
        app_name: Application name
        session_service: Session service instance

    Returns:
        A new Runner instance
    """
    from agent.agent import root_agent
    return Runner(app_name=app_name, agent=root_agent, session_service=session_service)


async def scenario_1_cancel_during_leader_planning() -> None:
    """Scenario 1: Cancel while team leader is planning (LLM streaming).

    This scenario uses asyncio.Event to trigger cancellation after receiving
    the first 10 events during leader's streaming response.
    Each query creates a new runner instance.

    Queries:
    1. Ask for content creation (will be cancelled after 10 events)
    2. Ask "what happened?" to verify team state
    """

    print("📋 Scenario 1: Cancel During Leader planning (TeamAgent)")
    print("-" * 80)

    # Common settings for this scenario
    app_name = "content_team_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Create session service (shared across queries)
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    # Query 1: Ask for content (will be cancelled during leader planning)
    query1 = "Introduce yourself in details."
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
    runner1 = create_runner(app_name, session_service)

    # Create background task to run agent
    async def run_query1() -> None:
        await run_agent(runner1, user_id, session_id, query1, event_count_callback=on_event_count)

    # Start agent in background
    task = asyncio.create_task(run_query1())

    # Wait for 10 events using direct wait_for timeout.
    print("⏳ Waiting for first 10 events...")
    try:
        await asyncio.wait_for(event_threshold_reached.wait(), timeout=10)
        # Cancel during leader planning
        runner2 = create_runner(app_name, session_service)
        print("\n⏸️  Requesting cancellation after 10 events...")
        success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id)
        print(f"✓ Cancellation requested: {success}")
    except asyncio.TimeoutError:
        print("\n⚠️ Event threshold not reached within 10.0s. "
              "Skip cancellation to avoid hanging.")

    await task

    print()
    print("💡 Result: Leader's partial response and cancellation record saved to team memory")
    print()

    # Query 2: Ask "what happened?" to verify team state
    query2 = "what happened?"
    print(f"📝 User Query 2: {query2}")
    print()

    # Create new runner for query 2
    runner2 = create_runner(app_name, session_service)
    await run_agent(runner2, user_id, session_id, query2)

    print("💡 Result: Team can respond with context from previous cancelled run")
    print("-" * 80)
    print()


async def scenario_2_cancel_during_member_execution() -> None:
    """Scenario 2: Cancel while team member is executing tools.

    This scenario uses asyncio.Event to synchronize cancellation exactly when
    a member's tool execution starts. Each query creates a new runner instance.

    Queries:
    1. Ask for research-based content (will be cancelled during member tool execution)
    2. Ask "what happened?" to verify team state
    """

    print("📋 Scenario 2: Cancel During Member Tool Execution (TeamAgent)")
    print("-" * 80)

    # Common settings for this scenario
    app_name = "content_team_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Create session service (shared across queries)
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    # Query 1: Ask for research (will be cancelled during member execution)
    query1 = "Research renewable energy and write a short article about it. Research should be simple."
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User Query 1: {query1}")
    print()

    # Event to signal when member tool is called
    member_tool_detected = asyncio.Event()

    # Create callback for tool call detection
    async def on_tool_call(tool_name: str) -> None:
        # Detect member tools (search_web, check_grammar)
        if tool_name in ["search_web", "check_grammar"]:
            print(f"⏳ [Member tool '{tool_name}' detected...]")
            member_tool_detected.set()

    # Create new runner for query 1
    runner1 = create_runner(app_name, session_service)

    # Create background task to run agent
    async def run_query1() -> None:
        await run_agent(runner1, user_id, session_id, query1, tool_call_callback=on_tool_call)

    # Start agent in background
    task = asyncio.create_task(run_query1())

    # Wait for member tool detection using direct wait_for timeout.
    print("⏳ Waiting for member tool execution to start...")
    try:
        await asyncio.wait_for(member_tool_detected.wait(), timeout=10)
        runner2 = create_runner(app_name, session_service)
        print("\n⏸️  Member tool detected! Requesting cancellation during member execution...")
        success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id)
        print(f"✓ Cancellation requested: {success}")
    except asyncio.TimeoutError:
        print("\n⚠️ Member tool not detected within 10.0s. "
              "Skip cancellation to avoid hanging.")

    await task

    print()
    print("💡 Result: Member's partial response recorded in team memory with cancellation context")
    print()

    # Query 2: Ask "what happened?" to verify team state
    query2 = "what happened?"
    print(f"📝 User Query 2: {query2}")
    print()

    # Create new runner for query 2
    runner2 = create_runner(app_name, session_service)
    await run_agent(runner2, user_id, session_id, query2)

    print("💡 Result: Team can resume with partial delegation records preserved")
    print("-" * 80)
    print()


async def run_with_cancellation_demo() -> None:
    """Demonstrate team cancellation in realistic scenarios."""

    print("=" * 80)
    print("🎯 TeamAgent Cancellation Demo")
    print("=" * 80)
    print()

    # Scenario 1: Cancel during leader planning
    await scenario_1_cancel_during_leader_planning()

    # Scenario 2: Cancel during member execution
    await scenario_2_cancel_during_member_execution()

    print()
    print("=" * 80)
    print("✅ Demo completed!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_with_cancellation_demo())
