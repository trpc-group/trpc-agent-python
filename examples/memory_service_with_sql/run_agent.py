#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Run the weather query agent demo"""
import asyncio
import os

from dotenv import load_dotenv
from trpc_agent_sdk.memory import MemoryServiceConfig
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


def create_memory_service(is_async: bool = False):
    """Create session service"""
    # DROP DATABASE IF EXISTS trpc_agent_memory;
    # CREATE DATABASE trpc_agent_memory;
    # USE trpc_agent_memory;
    # SELECT * FROM mem_events;
    # Build MySQL connection URL from environment variables
    # Required driver: `pymysql` (install via `pip install pymysql`)
    db_user = os.environ.get("MYSQL_USER", "root")
    db_password = os.environ.get("MYSQL_PASSWORD", "")
    db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    db_port = os.environ.get("MYSQL_PORT", "3306")
    db_name = os.environ.get("MYSQL_DB", "trpc_agent_memory")
    # Example: mysql+pymysql://user:pass@host:3306/dbname?charset=utf8mb4
    db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    # db_url = f"mysql+aiomysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    memory_service_config = MemoryServiceConfig(
        enabled=True,
        ttl=MemoryServiceConfig.create_ttl_config(enable=True, ttl_seconds=20, cleanup_interval_seconds=10),
    )
    memory_service = SqlMemoryService(
        memory_service_config=memory_service_config,
        is_async=is_async,
        db_url=db_url,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    return memory_service


async def run_weather_agent():
    """Run the weather query agent demo"""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    memory_service = create_memory_service()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service, memory_service=memory_service)

    user_id = "sql_memory_user"
    current_session_id = "sql_memory_session"

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
    print("=" * 60)
    print("First run")
    print("=" * 60)
    await run_weather_agent()
    await asyncio.sleep(2)
    print("=" * 60)
    print("Second run")
    print("=" * 60)
    await run_weather_agent()
    await asyncio.sleep(30)
    print("=" * 60)
    print("Third run")
    print("=" * 60)
    await run_weather_agent()
    # wait for the memory to be expired
    await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
