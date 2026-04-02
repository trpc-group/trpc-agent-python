#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Run the weather query agent demo"""
import asyncio

from dotenv import load_dotenv
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import MemoryServiceConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


def create_memory_service():
    """Create memory service"""

    memory_service_config = MemoryServiceConfig(
        enabled=True,
        ttl=MemoryServiceConfig.create_ttl_config(enable=True, ttl_seconds=10, cleanup_interval_seconds=10),
    )
    memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)
    return memory_service


async def run_weather_agent(memory_service: InMemoryMemoryService):
    """Run the weather query agent demo"""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service, memory_service=memory_service)

    user_id = "in_memory_user"
    current_session_id = "in_memory_session"

    # Demo query list
    demo_queries = [
        "Do you remember my name?",
        "Do you remember my favorite color?",
        "what is the weather like in paris?",
        "Hello! My name is Alice. What's your name?",
        "Do you remember my name?",
        "Hello! My favorite color is blue. What's your favorite color?",
        "Do you remember my favorite color?",
    ]

    for index, query in enumerate(demo_queries):
        # Use a new session for each query

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id,
                                            session_id=f"{current_session_id}_{index}",
                                            new_message=user_content):
            # Check if event.content exists
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                # Skip the reasoning part; the output is already generated when partial=True
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # Uncomment to get the full text output of the LLM
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


async def main():
    memory_service = create_memory_service()
    print("=" * 60)
    print("First run")
    print("=" * 60)
    await run_weather_agent(memory_service)
    await asyncio.sleep(2)
    print("=" * 60)
    print("Second run")
    print("=" * 60)
    await run_weather_agent(memory_service)
    await asyncio.sleep(30)
    print("=" * 60)
    print("Third run")
    print("=" * 60)
    await run_weather_agent(memory_service)


if __name__ == "__main__":
    asyncio.run(main())
