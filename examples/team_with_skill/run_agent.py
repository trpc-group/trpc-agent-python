#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Content Team example with leader skill support."""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_team_demo() -> None:
    """Run the team_with_skill demo conversation."""
    app_name = "content_team_with_skill_demo"

    from agent.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    demo_queries = [
        ("Please follow this mandatory process: "
         "first use skill `leader-research` and run "
         "`bash scripts/gather_points.sh \"renewable energy and AI trends in current year\" "
         "out/leader_notes.txt` with output file `out/leader_notes.txt`; "
         "then delegate to researcher and writer to produce a short final article."),
    ]

    print("=" * 70)
    print("Content Team With Skill Demo")
    print("=" * 70)
    print(f"\nSession ID: {session_id[:8]}...")
    print("\nThis demo shows leader using skills to extend context before delegation.")
    print("-" * 70)

    for index, query in enumerate(demo_queries, 1):
        print(f"\n[Turn {index}] User: {query}")
        print("-" * 40)
        user_content = Content(parts=[Part.from_text(text=query)])
        current_author = None

        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
        ):
            if event.content and event.content.parts:
                if not event.partial:
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n[{event.author}] Tool: {part.function_call.name}, "
                                  f"Args: {part.function_call.args}")
                            current_author = event.author
                        elif part.function_response:
                            current_author = event.author
                            print(f"\n[{event.author}] Tool Response: {part.function_response}")
                else:
                    for part in event.content.parts:
                        if part.text:
                            if current_author != event.author:
                                current_author = event.author
                                print(f"\n[{current_author}] ", end="")
                            print(f"{part.text}", end="", flush=True)

        print("\n")

    print("=" * 70)
    print("Demo completed!")
    print("=" * 70)
    await runner.close()


if __name__ == "__main__":
    print("Content Team With Skill Example")
    print("Demonstrates: Leader skills + Team delegation")
    print()
    asyncio.run(run_team_demo())
