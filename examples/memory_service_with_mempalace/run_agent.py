#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the weather query agent demo"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.memory import MemoryServiceConfig
from trpc_agent_sdk.memory.mempalace_memory_service import MempalaceMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()

sys.path.append(str(Path(__file__).parent))


def _truncate_tool_response(response, max_length: int = 256) -> str:
    """Limit verbose tool responses in demo output."""
    text = str(response)
    if len(text) <= max_length:
        return text
    return text[:max_length]


def create_memory_service():
    """Create session service"""

    memory_service_config = MemoryServiceConfig(
        ttl=MemoryServiceConfig.create_ttl_config(enable=True, ttl_seconds=20, cleanup_interval_seconds=20),
        enabled=True,
    )
    memory_service = MempalaceMemoryService(
        memory_service_config=memory_service_config,
        wing="trpc-agent",
        room="conversations",
        store_only_model_visible=True,
    )
    return memory_service


async def run_weather_agent(memory_service: MempalaceMemoryService):
    """Run the weather query agent demo"""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service, memory_service=memory_service)

    user_id = "mempalace_memory_user"
    current_session_id = "mempalace_memory_session"

    # Demo query list
    demo_queries = [
        "Do you remember my name?",
        "Do you remember my favorite color?",
        "What is the weather like in paris?",
        "Hello! My name is Alice. Please remember my name.",
        "Now, do you still remember my name?",
        "Hello! My favorite color is blue. Please remember my favorite color.",
        "Now, do you still remember my favorite color?",
    ]

    for index, query in enumerate(demo_queries):
        # Use a new session for each query

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        agent_context = AgentContext()
        session_id = f"{current_session_id}_{index}"
        # set_mem0_filters(agent_context, {"session_id": session_id})
        async for event in runner.run_async(agent_context=agent_context,
                                            user_id=user_id,
                                            session_id=session_id,
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
                    print(f"📊 [Tool Result: {_truncate_tool_response(part.function_response.response)}]")
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
    # await memory_service.delete_memory(wing="trpc-agent", room="conversations")


if __name__ == "__main__":
    asyncio.run(main())
