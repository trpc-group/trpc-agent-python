#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import json
import uuid

from agent.agent import UserProfileOutput
from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_agent_with_schema():
    """Run agent with schema，showcase the schema functionality"""

    app_name = "profile_analysis_demo"
    user_id = "demo_user"

    from agent.agent import create_agent
    agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    # User profile
    profile = {
        "name": "Zhang San",
        "age": 28,
        "email": "zhangsan@example.com",
        "interests": ["programming", "fitness"],
        "location": "Beijing",
    }

    current_session_id = str(uuid.uuid4())

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=current_session_id,
        state={"user_name": profile["name"]},
    )

    print(f"🆔 Session ID: {current_session_id[:8]}...")
    print(f"📝 User profile:\n {json.dumps(profile, indent=4, ensure_ascii=False)}")

    # Convert the input data into a JSON string (input_schema requirements)
    json_input = json.dumps(profile, ensure_ascii=False)
    user_content = Content(parts=[Part.from_text(text=json_input)])

    print("🤖 Analysis result: ", end="", flush=True)

    try:
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                elif part.function_call:
                    print(f"\n🔧 [Call tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool result: {part.function_response.response}]")

        # Check whether the result is saved to the session state
        session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=current_session_id)
        if session and session.state and agent.output_key in session.state:
            saved_result = session.state[agent.output_key]
            user_profile = UserProfileOutput.model_validate_json(saved_result)
            print(f"💾 Get UserProfileOutput: {user_profile}")

    except Exception as e:
        print(f"\n❌ Analysis error: {e}")

    await runner.close()

    print("\n" + "-" * 60)


# ============================================================================
# Agent Without Tools Demo
# ============================================================================


async def run_agent_without_tools():
    """Run tool-free agent demo: direct JSON output"""

    print("\n🚀 Agent Without Tools - Direct JSON Output Demo")
    print("=" * 60)

    app_name = "direct_profile_analysis_demo"
    user_id = "demo_user"

    from agent.agent import create_agent_without_tools
    agent = create_agent_without_tools()
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    # User profile
    profile = {
        "name": "Wang Wu",
        "age": 35,
        "email": "wangwu@example.com",
        "interests": ["reading", "traveling", "photography", "cooking"],
        "location": "Shenzhen",
    }

    current_session_id = str(uuid.uuid4())

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=current_session_id,
        state={"user_name": profile["name"]},
    )

    print(f"🆔 Session ID: {current_session_id[:8]}...")
    print(f"📝 User profile:\n {json.dumps(profile, indent=4, ensure_ascii=False)}")
    print("🤖 Direct JSON analysis result: ", end="", flush=True)

    # Convert the input data into a JSON string (input_schema requirements)
    json_input = json.dumps(profile, ensure_ascii=False)
    user_content = Content(parts=[Part.from_text(text=json_input)])

    try:
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                elif part.function_call:
                    print(f"\n🔧 [Call tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool result: {part.function_response.response}]")

        session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=current_session_id)
        if session and session.state and agent.output_key in session.state:
            saved_result = session.state[agent.output_key]
            user_profile = UserProfileOutput.model_validate_json(saved_result)
            print(f"\n💾 Get UserProfileOutput: {user_profile}")

    except Exception as e:
        print(f"\n❌ Analysis error: {e}")

    await runner.close()

    print("\n" + "-" * 60)


# ============================================================================
# AgentTool Usage Example
# ============================================================================


async def run_agent_tool_with_schema():
    """Demonstrate wrapping schema-enabled agent via AgentTool"""

    print("\n🔧 AgentTool with Schema example")
    print("=" * 60)

    # Create a main agent to use this tool
    from agent.agent import create_agent_tool_with_schema
    main_agent = create_agent_tool_with_schema()

    session_service = InMemorySessionService()
    runner = Runner(app_name="agent_tool_demo", agent=main_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Test Data(Natural language input) for the main agent to construct UserProfileInput
    description = "My name is Li Si, I'm 32 years old, my email is lisi@example.com, I like reading, traveling and photography, and I live in Shanghai."
    user_content = Content(parts=[Part.from_text(text=description)])

    print(f"\n📝 Extract user profile information: {description}")
    try:
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                elif part.function_call:
                    print(f"\n🔧 [Call tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool result: {part.function_response.response}]")

    except Exception as e:
        print(f"\n❌ Runtime error: {e}")

    print("\n" + "-" * 30)

    await runner.close()
    print("\n" + "-" * 60)


async def main():
    print("\n🚀 Start running Agent Schema example...")

    await run_agent_with_schema()

    await run_agent_without_tools()

    await run_agent_tool_with_schema()

    print("🎉 Successfully running all examples!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"\n❌ Runtime error: {e}")
        import traceback

        traceback.print_exc()
