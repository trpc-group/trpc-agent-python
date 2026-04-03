#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()

current_path = Path(__file__).parent
sys.path.append(str(current_path))


async def run_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    user_input: str,
) -> None:
    """Run the agent and print results."""
    user_content = Content(parts=[Part.from_text(text=user_input)])

    print(f"👤 User: {user_input}")
    print("🤖 Agent: ", end="", flush=True)

    author_print = False
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        if event.partial:
            if not author_print:
                print(f"\n[{event.author}]: ", end="", flush=True)
                author_print = True
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
        else:
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.function_call:
                        print(f"\n🔧 [Call tool: {part.function_call.name}]")
                    elif part.function_response:
                        print(f"📊 [Tool result: {part.function_response.response}]")

    print()


# ===== Example 1: Template substitution - use State in Instruction =====
async def use_state_in_instruction():
    """Example 1: Demonstrate template reference functionality"""
    print("=" * 60)
    print("Example 1: Template reference - using State in Instruction")
    print("=" * 60)
    from agent.agent import create_agent
    name = "personalized_assistant"
    description = "A helpful assistant"
    instruction = "You are a helpful assistant. Answer the user's question. Username is {username}"
    agent = create_agent(name=name, description=description, instruction=instruction)
    session_service = InMemorySessionService()
    runner = Runner(app_name="personalized_app", agent=agent, session_service=session_service)

    user_id = "Alice"
    session_id = str(uuid.uuid4())

    # Set initial state
    await session_service.create_session(
        app_name="personalized_app",
        user_id=user_id,
        session_id=session_id,
        state={
            "username": user_id,
        },
    )

    user_input = "Can you tell me my name?"
    await run_agent(runner=runner, user_id=user_id, session_id=session_id, user_input=user_input)
    print("\n")


# ===== Example 2: Modify State in tools =====
async def use_state_in_tool():
    """Example 2: Demonstrate modifying State in tools"""
    print("=" * 60)
    print("Example 2: Modifying State in tools")
    print("=" * 60)

    from agent.agent import create_agent
    from agent.tools import update_user_preference, get_current_preferences
    name = "preference_agent"
    description = "Preference manager"
    instruction = "You are a preference manager. You can help users set and view their preferences."
    tools = [update_user_preference, get_current_preferences]
    agent = create_agent(name=name, description=description, instruction=instruction, tools=tools)
    session_service = InMemorySessionService()
    runner = Runner(app_name="preference_app", agent=agent, session_service=session_service)

    user_id = "bob"
    session_id = str(uuid.uuid4())

    # Test setting preferences
    user_input = "Please help me set the theme preference to dark mode"
    await run_agent(runner=runner, user_id=user_id, session_id=session_id, user_input=user_input)

    # View current preferences
    user_input = "Please show me all my current preference settings"
    await run_agent(runner=runner, user_id=user_id, session_id=session_id, user_input=user_input)
    print("\n")


# ===== Example 3: Multi-agent collaboration - output_key =====
async def use_state_in_multi_agent():
    """Example 3: Demonstrate multi-Agent collaboration - using output_key"""
    print("=" * 60)
    print("Example 3: Multi-Agent collaboration - using output_key")
    print("=" * 60)

    from agent.agent import create_chain_agent
    chain_agent = create_chain_agent()

    session_service = InMemorySessionService()
    app_name = "collaboration_app"
    runner = Runner(app_name=app_name, agent=chain_agent, session_service=session_service)

    user_id = "charlie"
    session_id = str(uuid.uuid4())

    user_input = "I want to learn programming but don't know where to start, and I'm worried it will be too hard to stick with"
    await run_agent(runner=runner, user_id=user_id, session_id=session_id, user_input=user_input)

    # Show persisted state
    session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    if session and session.state:
        print("\n📊 Collaboration results saved in state:")
        if "analysis_result" in session.state:
            print(f"   📝 Analysis result: {session.state['analysis_result'][:50]}...")
        if "solution_plan" in session.state:
            print(f"   📋 Solution: {session.state['solution_plan'][:50]}...")
    print("\n")


# ===== Example 4: Different State scopes =====
async def use_state_in_different_scopes():
    """Example 4: Demonstrate different State scopes"""
    print("=" * 60)
    print("Example 4: State scope demonstration")
    print("=" * 60)

    from agent.agent import create_agent
    from agent.tools import set_state_at_different_levels
    from agent.utils import print_session_state
    name = "state_demo_agent"
    description = "Demonstrate the use of different levels of state"
    instruction = "You are a state display assistant. You can help users set and view different levels of state."
    tools = [set_state_at_different_levels]
    agent = create_agent(name=name, description=description, instruction=instruction, tools=tools)
    session_service = InMemorySessionService()
    app_name = "scope_demo_app"
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    # Step 1: Set initial state (user1, session1)
    print("\n🔹 Step 1: Set initial state (user1, session1)")
    user1_id = "user1"
    session1_id = str(uuid.uuid4())

    # Pre-set some app-level state
    await session_service.create_session(
        app_name=app_name,
        user_id=user1_id,
        session_id=session1_id,
        state={
            "app:value": "Application level data",
        },
    )

    # Use the agent to set state at each level
    user_input = "Please help me set the state at each level: session level state is 'session1 data', user level state is 'user1 preference', application level state remains unchanged"
    await run_agent(runner=runner, user_id=user1_id, session_id=session1_id, user_input=user_input)
    await print_session_state(session_service, app_name, user1_id, session1_id, "user1 session1 state")

    # Step 2: Same user, new session — user-level state should persist, session-level should reset
    print("\n🔹 Step 2: Same user, new session (user1, session2)")
    session2_id = str(uuid.uuid4())

    user_input = "Hello"
    await run_agent(runner=runner, user_id=user1_id, session_id=session2_id, user_input=user_input)
    await print_session_state(session_service, app_name, user1_id, session2_id, "user1 session2 state")

    # Step 3: New user, new session — only app-level state should persist
    print("\n🔹 Step 3: New user, new session (user2, session3)")
    user2_id = "user2"
    session3_id = str(uuid.uuid4())

    user_input = "Hello"
    await run_agent(runner=runner, user_id=user2_id, session_id=session3_id, user_input=user_input)
    await print_session_state(session_service, app_name, user2_id, session3_id, "user2 session3 state")


async def run_agent_demo():
    """Run all State usage examples"""
    try:
        await use_state_in_instruction()
        await use_state_in_tool()
        await use_state_in_multi_agent()
        await use_state_in_different_scopes()
    except Exception as e:
        print(f"❌ Example run error: {e}")


if __name__ == "__main__":
    asyncio.run(run_agent_demo())
