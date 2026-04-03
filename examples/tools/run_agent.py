#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools demo"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_agent_tool_demo():
    """Run the AgentTool demo agent"""

    app_name = "agent_tool_demo"

    from agent.agent import create_agent_tool_agent

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=create_agent_tool_agent(), session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "Please translate this to Chinese: Artificial intelligence is changing our world.",
    ]
    main_agent = create_agent_tool_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="agent_tool_demo", agent=main_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # 测试场景
    test_scenarios = [
        "请将这段中文翻译成英文：人工智能正在改变我们的世界。",
        "Please translate this to Chinese: Hello, how are you today?",
    ]

    for i, query in enumerate(test_scenarios, 1):
        print(f"\n Test {i}: {query}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            if event.content and event.content.parts and event.author != "user":
                if event.partial:
                    # Streaming output
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    # Tool call and result
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [Tool call: {part.function_call.name}]")
                        elif part.function_response:
                            print(f"📊 [Tool result: {part.function_response.response}]")

        print("\n" + "-" * 50)

    await runner.close()
    print("\n✅ AgentTool demo completed!")


async def run_function_tool_demo():
    """Run the Function Tool demo agent"""

    from agent.agent import create_function_tool_agent

    print("🔧 Function Tool demo")
    print("=" * 60)
    print("This demo shows the two usages of Function Tool:")
    print("• Directly package asynchronous functions to create tools (get_weather)")
    print("• Decorator register asynchronous tools (get_session_info)")
    print("=" * 60)

    agent = create_function_tool_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="function_tool_demo", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Test queries
    test_queries = [
        "Please get the weather in Beijing",
        "Please get the postal code in Guangdong Shenzhen",
        "Please get the session information",
        "Now calculate 15 * 3.5",
    ]

    for i, query in enumerate(test_queries, 1):
        print(f"\n Test {i}: {query}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            if event.content and event.content.parts and event.author != "user":
                if event.partial:
                    # Streaming output
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    # Full event
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [Tool call: {part.function_call.name}]")
                            print(f"    Args: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [Tool result: {part.function_response.response}]")

        print("\n" + "-" * 50)

    await runner.close()
    print("\n✅ Function Tool demo completed!")


async def run_langchain_tool_demo():
    """Run the LangChain Tool demo agent"""

    print("🔎 LangChain Tool demo")
    print("=" * 60)
    print("This demo shows how to use LangChain tool to search the internet")
    print("Environment requirements: TAVILY_API_KEY must be set in environment variables")
    print("=" * 60)

    # Create Agent and Runner
    from agent.agent import create_langchain_tool_agent
    agent = create_langchain_tool_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="langchain_tool_demo", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Test queries
    test_queries = [
        "Please search for the latest news in the AI field",
    ]

    for i, query in enumerate(test_queries, 1):
        print(f"\n📝 Test {i}: {query}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            if event.content and event.content.parts and event.author != "user":
                if event.partial:
                    # Streaming output
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    # Full event
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [Tool call: {part.function_call.name}]")
                            print(f"    Args: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [Tool result: {part.function_response.response}]")

        print("\n" + "-" * 50)

    await runner.close()
    print("\n✅ LangChain Tool demo completed!")


async def run_toolset_demo():
    """Run the ToolSet demo agent"""

    print("=" * 60)
    print("🔧 ToolSet demo")
    print("=" * 60)

    # Create Agent and Runner
    from agent.agent import create_toolset_agent
    agent = create_toolset_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="weather_toolset_demo", agent=agent, session_service=session_service)

    # Test different types of users
    test_scenarios = [
        {
            "user_id":
            "basic_user",
            "user_type":
            "basic",
            "queries": [
                "Please get the current weather in Beijing",
                "Please get the weather forecast for Beijing for the next 5 days",
            ],
        },
        {
            "user_id": "vip_user",
            "user_type": "vip",
            "queries": [
                "Please get the weather forecast for Beijing for the next 5 days",
            ],
        },
    ]

    for scenario in test_scenarios:
        user_id = scenario["user_id"]
        user_type = scenario["user_type"]
        session_id = str(uuid.uuid4())

        print(f"\n👤 User type: {user_type.upper()}")
        print("=" * 40)

        # Create Session and set user state
        await session_service.create_session(
            app_name="weather_toolset_demo",
            user_id=user_id,
            session_id=session_id,
            state={"user_type": user_type},
        )

        # Execute test queries
        for i, query in enumerate(scenario["queries"], 1):
            print(f"\n📝 Test {i}: {query}")
            print("🤖 Assistant: ", end="", flush=True)

            user_content = Content(parts=[Part.from_text(text=query)])

            async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
                if event.content and event.content.parts and event.author != "user":
                    if event.partial:
                        # Streaming output
                        for part in event.content.parts:
                            if part.text:
                                print(part.text, end="", flush=True)
                    else:
                        # Full event
                        for part in event.content.parts:
                            if part.function_call:
                                print(f"\n🔧 [Tool call: {part.function_call.name}]")
                                print(f"    Args: {part.function_call.args}")
                            elif part.function_response:
                                print(f"📊 [Tool result: {part.function_response.response}]")

            print("\n" + "-" * 30)

    # Need to close manually
    await runner.close()
    print("\n✅ ToolSet demo completed!")


# =============================================================================
# Main function
# =============================================================================


async def main():
    """Main function"""
    try:
        await run_agent_tool_demo()
        await run_function_tool_demo()
        await run_langchain_tool_demo()
        await run_toolset_demo()
    except KeyboardInterrupt:
        print("\n\n👋 Demo interrupted")
    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
