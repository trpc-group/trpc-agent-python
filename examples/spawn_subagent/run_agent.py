# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Run the spawn_subagent demo.

Usage::

    python run_agent.py                  # default mode (default archetype only)
    python run_agent.py --mode code      # code-defined security-auditor + Explore/Plan
    python run_agent.py --mode md        # MD-defined security-auditor + Explore/Plan

The demo points sub-agents at the shared sample repo (``./sample_repo/``)
so output is fast, predictable, and never depends on the larger
trpc-agent-python codebase.
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
SAMPLE_REPO = os.path.join(EXAMPLE_DIR, "sample_repo")
if EXAMPLE_DIR not in sys.path:
    sys.path.insert(0, EXAMPLE_DIR)

def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate long tool output for display."""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... (truncated, total {len(text)} chars)"


# Queries per mode — simple tasks (parent handles directly) vs complex
# tasks (delegated to a sub-agent).
_SHARED_AGENT_QUERIES = [
    # Triggers: security-auditor
    "I need a security code audit of the authentication system in "
    "auth.py and app.py. Check for vulnerabilities, hardcoded secrets, "
    "and missing authorization checks.",
    # Triggers: Explore (built-in archetype)
    "How does authentication and user identity work in this codebase? "
    "I need to understand every file and function involved across "
    "multiple naming conventions.",
]

_QUERIES = {
    "default": [
        # Simple task: parent handles directly (ReadTool), no sub-agent.
        "What does the file auth.py do? Give me a one-sentence summary.",
        # Triggers: default archetype (explicit "Use a sub-agent" in the query).
        "Use a sub-agent to explore this codebase: find all functions that "
        "accept a 'user_id' parameter, and report which files they are in "
        "and what they do.",
    ],
    "code": _SHARED_AGENT_QUERIES,
    "md": _SHARED_AGENT_QUERIES,
}


async def run_demo(mode: str):
    app_name = "spawn_subagent_demo"

    if mode == "code":
        from agent.agent import create_code_agent
        agent = create_code_agent()
    elif mode == "md":
        from agent.agent import create_md_agent
        agent = create_md_agent()
    else:
        from agent.agent import create_default_agent
        agent = create_default_agent()

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"
    queries = _QUERIES.get(mode, _QUERIES["default"])

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
                            print(f"\n\n\U0001F527 [Invoke Tool:: {part.function_call.name}{(_truncate(part.function_call.args))}]\n")
                        elif part.function_response:
                            print(f"\n\U0001F4CA [Tool Result: {_truncate(part.function_response.response)}]\n")

        print(f"\n{'─' * 60}\n")

    await runner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SpawnSubAgentTool demo")
    parser.add_argument(
        "--mode", choices=["default", "code", "md"], default="default",
        help="Which agent configuration to run (default: default)"
    )
    args = parser.parse_args()

    os.chdir(SAMPLE_REPO)
    asyncio.run(run_demo(args.mode))
