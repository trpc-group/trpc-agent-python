# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Run the dynamic_agent demo.

Usage::

    python run_agent.py                  # minimal mode (default)
    python run_agent.py --mode bounded   # bounded / progressive disclosure
"""

import argparse
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
if EXAMPLE_DIR not in sys.path:
    sys.path.insert(0, EXAMPLE_DIR)


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate long tool output for display."""
    return text
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... (truncated, total {len(text)} chars)"


_QUERIES = {
    "minimal": [
        # Simple task: orchestrator may call word_count directly.
        'Count the words in: "the quick brown fox".',
        # Delegate self-contained subtasks via dynamic_agent.
        "Use a sub-agent to compute (123 * 456) + 789. Grant it only the calculator.",
        "Use a sub-agent to tell me the current time in UTC.",
    ],
    "bounded": [
        "Use a sub-agent to compute (123 * 456) + 789. Grant it only the calculator.",
        'Use one sub-agent to compute 50 * 12, and a separate sub-agent to count '
        'the words in "the quick brown fox jumps". Grant each only the tool it needs.',
    ],
}


async def run_demo(mode: str):
    app_name = "dynamic_agent_demo"

    if mode == "bounded":
        from agent.agent import create_bounded_agent
        agent = create_bounded_agent()
    else:
        from agent.agent import create_minimal_agent
        agent = create_minimal_agent()

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"
    queries = _QUERIES.get(mode, _QUERIES["minimal"])

    for query in queries:
        current_session_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=current_session_id,
        )

        print(f"\n{'=' * 60}")
        print(f"\U0001F194 Mode: {mode} | Session ID: {current_session_id[:8]}...")
        print(f"{'-' * 60}")
        print(f"\U0001F4DD User: {query}")

        user_content = Content(parts=[Part.from_text(text=query)])
        print("\U0001F916 Assistant: ", end="", flush=True)
        async for event in runner.run_async(
            user_id=user_id,
            session_id=current_session_id,
            new_message=user_content,
        ):
            if event.content and event.content.parts and event.author != "user":
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    for part in event.content.parts:
                        if part.thought:
                            continue
                        if part.function_call:
                            print(
                                f"\n\n\U0001F527 [Invoke Tool:: {part.function_call.name}"
                                f"{_truncate(part.function_call.args)}]\n"
                            )
                        elif part.function_response:
                            print(
                                f"\n\U0001F4CA [Tool Result: "
                                f"{_truncate(part.function_response.response)}]\n"
                            )

        print(f"\n{'─' * 60}\n")

    await runner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DynamicAgentTool demo")
    parser.add_argument(
        "--mode", choices=["minimal", "bounded"], default="minimal",
        help="minimal: workspace tools + dynamic_agent; bounded: only dynamic_agent",
    )
    args = parser.parse_args()
    asyncio.run(run_demo(args.mode))
