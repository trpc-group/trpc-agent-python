#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Run the weather query agent demo"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from mem0 import AsyncMemory
from mem0 import AsyncMemoryClient
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.memory import MemoryServiceConfig
from trpc_agent_sdk.memory.mem0_memory_service import Mem0MemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part
# Load environment variables from the .env file
load_dotenv()

sys.path.append(str(Path(__file__).parent))


def create_memory_service(use_mem0_platform: bool = False):
    """Create session service"""

    from agent.config import get_memory_config
    from agent.config import get_mem0_platform_config
    if use_mem0_platform:
        mem0_client_config = get_mem0_platform_config()
        mem0_client = AsyncMemoryClient(api_key=mem0_client_config['api_key'], host=mem0_client_config['host'])
    else:
        mem0_client_config = get_memory_config()
        mem0_client = AsyncMemory(config=mem0_client_config)
    memory_service_config = MemoryServiceConfig(
        ttl=MemoryServiceConfig.create_ttl_config(enable=False, ttl_seconds=20, cleanup_interval_seconds=20),
        enabled=True,
    )
    memory_service = Mem0MemoryService(
        mem0_client=mem0_client,
        memory_service_config=memory_service_config,
        infer=True,
    )
    return memory_service


async def run_weather_agent(memory_service: Mem0MemoryService):
    """Run the weather query agent demo"""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service, memory_service=memory_service)

    user_id = "mem0_memory_user"
    current_session_id = "in_memory_session"

    # 演示查询列表
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
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # Uncomment to get the full text output of the LLM
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


async def main():
    use_mem0_platform = True
    memory_service = create_memory_service(use_mem0_platform=use_mem0_platform)
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
