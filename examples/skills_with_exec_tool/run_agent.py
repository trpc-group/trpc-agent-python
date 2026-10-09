#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demonstrate interactive skill execution and output collection."""

import asyncio
import json
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.artifacts import InMemoryArtifactService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_skill_exec_demo() -> None:
    """Run one interactive skill and collect its output artifact."""
    from agent.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(
        app_name="skill_exec_agent_demo",
        agent=root_agent,
        session_service=session_service,
        artifact_service=InMemoryArtifactService(),
    )
    session_id = str(uuid.uuid4())
    query = """
Use only the interactive-report skill for this task.

1. Load the skill documentation with skill_load.
2. Call skill_exec, not skill_run, with exactly these important arguments:
   - skill: interactive-report
   - command: python3 scripts/create_report.py
   - stdin: release-1.2.0\\n2\\n
   - yield_time_ms: 3000
   - output_files: ["out/report.txt"]
   - save_as_artifacts: true
   - artifact_prefix: "skill-exec-demo/"
3. Report the collected output_files and artifact_files from the final
   skill_exec result.
"""

    print(f"Session ID: {session_id}")
    print(f"User: {query}")
    print("Assistant: ", end="", flush=True)
    try:
        async for event in runner.run_async(
                user_id="demo_user",
                session_id=session_id,
                new_message=Content(parts=[Part.from_text(text=query)]),
        ):
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
                    args = json.dumps(part.function_call.args, ensure_ascii=False)
                    print(f"\n[Invoke Tool: {part.function_call.name}({args})]")
                elif part.function_response:
                    response = json.dumps(
                        part.function_response.response,
                        ensure_ascii=False,
                        default=str,
                    )
                    print(f"[Tool Result: {response}]")
        print()
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(run_skill_exec_demo())
