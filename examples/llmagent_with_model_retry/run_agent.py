# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the model retry weather agent example."""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_weather_agent() -> None:
    """Run the weather query agent with model-level retry enabled."""
    app_name = "model_retry_weather_demo"

    from agent.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={
            "user_name": user_id,
            "user_city": "Beijing",
        },
    )

    query = "What's the current weather in Beijing?"
    print(f"Session ID: {session_id[:8]}...")
    print(f"User: {query}")
    print("Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=query)])
    assistant_started = True

    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        if event.is_error():
            if assistant_started:
                print()
                assistant_started = False
            print(f"Error: {event.error_code}: {event.error_message}")
            continue

        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text and not part.thought:
                    if not assistant_started:
                        print("Assistant: ", end="", flush=True)
                        assistant_started = True
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.thought:
                continue
            if part.function_call:
                print(f"\nInvoke Tool: {part.function_call.name}({part.function_call.args})")
                assistant_started = False
            elif part.function_response:
                print(f"Tool Result: {part.function_response.response}")
            elif part.text and not assistant_started:
                print("Assistant: ", end="", flush=True)
                print(part.text, end="", flush=True)
                assistant_started = True

    print("\n")


if __name__ == "__main__":
    asyncio.run(run_weather_agent())
