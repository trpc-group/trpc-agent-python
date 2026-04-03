#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the weather query agent demo"""
import asyncio
import os

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


def create_session_service(is_async: bool = False):
    """Create session service"""
    # AUTH test # AUTH
    # FLUSHALL # clean
    # KEYS *
    db_user = os.environ.get("REDIS_USER", "")
    db_password = os.environ.get("REDIS_PASSWORD", "")
    db_host = os.environ.get("REDIS_HOST", "127.0.0.1")
    db_port = os.environ.get("REDIS_PORT", "6379")
    db_name = os.environ.get("REDIS_DB", "0")

    # Build Redis URL based on authentication requirements
    if db_password:
        if db_user:
            # Redis 6.0+ with ACL (username:password)
            db_url = f"redis://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        else:
            # Redis < 6.0 with password only
            db_url = f"redis://:{db_password}@{db_host}:{db_port}/{db_name}"
    else:
        # No authentication
        db_url = f"redis://{db_host}:{db_port}/{db_name}"

    session_config = SessionServiceConfig(
        max_events=1000,
        event_ttl_seconds=5,
        ttl=SessionServiceConfig.create_ttl_config(enable=True, ttl_seconds=5, cleanup_interval_seconds=5),
    )
    session_service = RedisSessionService(
        is_async=is_async,
        db_url=db_url,
        session_config=session_config,
    )
    return session_service


async def run_weather_agent():
    """Run the weather query agent demo"""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = create_session_service()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "redis_user"
    current_session_id = "redis_session_1"

    # Demonstrate query list
    demo_queries = [
        "Do you remember my name?",
        "Do you remember my favorite color?",
        "what is the weather like in paris?",
        "Hello! My name is Alice. What's your name?",
        "Do you remember my name?",
        "My favorite color is blue.",
        "Do you remember my favorite color?",
    ]

    for query in demo_queries:
        # Use a new session for each query

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
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
    await asyncio.sleep(10)
    print("=" * 60)
    print("Third run")
    print("=" * 60)
    await run_weather_agent()


if __name__ == "__main__":
    asyncio.run(main())
