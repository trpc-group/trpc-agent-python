# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run an Agent that calls tools generated from an OpenAPI document."""

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv(Path(__file__).with_name(".env"))


async def run_openapi_agent() -> None:
    """Run model-driven query and create operations against the mock API."""
    from agent import root_agent

    runner = Runner(
        agent=root_agent,
        app_name="openapi_tools_demo",
        session_service=InMemorySessionService(),
    )
    queries = [
        "Find pet pet-1 and tell me its name and species.",
        "Create a dog named Pixel and tell me the new pet ID.",
    ]

    try:
        for query in queries:
            session_id = str(uuid.uuid4())
            has_streamed_text = False
            print(f"Session: {session_id}")
            print(f"User: {query}")
            print("Assistant: ", end="", flush=True)

            async for event in runner.run_async(
                    user_id="demo_user",
                    session_id=session_id,
                    new_message=Content(parts=[Part.from_text(text=query)]),
            ):
                if not event.content or not event.content.parts:
                    continue
                if event.partial:
                    for part in event.content.parts:
                        if part.text and not part.thought:
                            has_streamed_text = True
                            print(part.text, end="", flush=True)
                    continue
                for part in event.content.parts:
                    if part.function_call:
                        print(f"\n[Invoke Tool: {part.function_call.name}"
                              f"({part.function_call.args})]")
                    elif part.function_response:
                        print(f"\n[Tool Result: {part.function_response.response}]")
                    elif part.text and not part.thought and not has_streamed_text:
                        print(part.text, end="", flush=True)
            print("\n" + "-" * 60)
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(run_openapi_agent())
