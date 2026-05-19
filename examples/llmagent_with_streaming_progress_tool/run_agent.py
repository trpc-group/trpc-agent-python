# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo: streaming progress events from a long-running tool.

Run with::

    cd examples/llmagent_with_streaming_progress_tool
    python run_agent.py

Make sure ``TRPC_AGENT_API_KEY``, ``TRPC_AGENT_BASE_URL`` and
``TRPC_AGENT_MODEL_NAME`` are set in your environment or .env file.
"""

import asyncio
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

sys.path.append(str(Path(__file__).parent))


async def run_streaming_progress_demo() -> None:
    """Issue one query and pretty-print every event surfaced by the runner.

    Three event kinds matter here:
    1. ``event.partial`` with ``custom_metadata.tool_progress`` → a *progress*
       chunk from the streaming tool. Print it live; do NOT treat it as a
       tool response.
    2. ``event.partial`` text from the LLM (no ``tool_progress`` marker) →
       streaming model output.
    3. ``partial=False`` events with a ``function_response`` part → the final
       tool result; with a ``text`` part → the model's final reply.
    """

    app_name = "streaming_progress_demo"
    from agent.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    await session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)

    query = "Please crawl https://example.com and fetch the first 5 pages."
    print("=" * 60)
    print(f"User: {query}")
    print("=" * 60)

    user_content = Content(parts=[Part.from_text(text=query)])

    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        meta = event.custom_metadata or {}

        # --- 1. Tool progress (partial, comes from StreamingProgressTool) ---
        if event.partial and meta.get("tool_progress"):
            payload = meta.get("payload")
            tool_name = meta.get("tool_name", "?")
            print(f"[{tool_name}] ⏳ {payload if payload is not None else event.get_text()}")
            continue

        if not event.content or not event.content.parts:
            continue

        # --- 2. Streaming LLM text ---
        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        # --- 3. Final events ---
        for part in event.content.parts:
            if part.function_call:
                print(f"\n[tool-call] {part.function_call.name}({part.function_call.args})")
            elif part.function_response:
                print(f"\n[tool-result] {part.function_response.name} → "
                      f"{part.function_response.response}")
            elif part.text:
                print(f"\nAssistant: {part.text}")

    print("\n" + "-" * 60)


if __name__ == "__main__":
    print("""
+--------------------------------------------------------------+
|        StreamingProgressTool Demo (long-running tool)        |
|                                                              |
| Watch the tool yield progress events live, then the LLM      |
| summarises the final result.                                 |
+--------------------------------------------------------------+
""")
    asyncio.run(run_streaming_progress_demo())
