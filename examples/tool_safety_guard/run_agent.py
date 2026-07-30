# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the safety-guarded Bash agent demo.

Set TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME in a local
``.env`` first, then::

    python run_agent.py

The first query is safe and runs; the second asks for a destructive command,
which the Tool Safety Guard filter blocks before Bash executes it.
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_demo() -> None:
    """Run a safe query and a blocked query through the guarded agent."""
    app_name = "tool_safety_guard_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    demo_queries = [
        "List the files in the current directory.",
        "Delete everything under the root directory with rm -rf /.",
    ]

    for query in demo_queries:
        session_id = str(uuid.uuid4())
        await session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)

        print(f"\n📝 User: {query}")
        user_content = Content(parts=[Part.from_text(text=query)])
        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            if not event.content or not event.content.parts:
                continue
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_demo())
