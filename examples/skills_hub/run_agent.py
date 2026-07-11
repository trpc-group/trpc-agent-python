#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Example demonstrating the Skill Hub (`trpc_agent_sdk.skills.hub`): fetching a
skill from GitHub on demand and using it like any locally-installed Agent
Skill.
"""
import asyncio
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

EXAMPLE_DIR = Path(__file__).resolve().parent
load_dotenv(EXAMPLE_DIR / ".env")

QUERY = """
Load the skill-creator skill and, without running any of its scripts,
summarize in a few bullet points: what it is for, and what top-level
files/folders it ships with.
"""


async def run_skill_hub_demo() -> None:
    """Fetch a skill via the Skill Hub, then run the demo agent against it."""
    from agent.agent import create_agent

    data_dir = EXAMPLE_DIR / "data"
    skills_dir = data_dir / "skills"

    # Remove any skill installed by a previous run so this demo always
    # re-downloads it via the Skill Hub.
    if data_dir.exists():
        shutil.rmtree(data_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)

    root_agent = create_agent(skills_dir)

    app_name = "skill_hub_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User: {QUERY.strip()}")
    print("🤖 Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=QUERY)])
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

    install_root = skills_dir / ".downloaded"
    print(f"\nSkill files fetched from GitHub via the Skill Hub into {install_root}:")
    skill_files = [f for f in sorted(install_root.rglob("*")) if f.is_file()]
    if skill_files:
        for skill_file in skill_files:
            print(f"- {skill_file.relative_to(install_root)}")
    else:
        print("- <none>")


if __name__ == "__main__":
    asyncio.run(run_skill_hub_demo())
