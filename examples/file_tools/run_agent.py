#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""File Tools Example.

This example demonstrates how to use file operation tools (Read, Write, Edit, Grep, Bash, Glob)
in TRPC Agent for file operations, text editing, and search functionality.
"""

import asyncio
import os
import shutil
import tempfile
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

from agent.agent import create_agent


async def run_file_tools_agent():
    """Run the file tools agent demo"""

    app_name = "file_tools_demo"

    # Create working directory in system temp directory
    system_temp = tempfile.gettempdir()
    work_dir = os.path.join(system_temp, "file_tools_demo")
    os.makedirs(work_dir, exist_ok=True)
    print(f"📁 Working directory: {work_dir}")

    try:
        # Create initial test files
        test_file = os.path.join(work_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Hello, World!\nThis is a test file.\nLine 3\n")

        config_file = os.path.join(work_dir, "config.ini")
        with open(config_file, "w") as f:
            f.write("[Database]\nhost=localhost\nport=5432\n")

        print(f"✅ Created test files: test.txt, config.ini")

        # Create agent with working directory
        agent = create_agent(work_dir=work_dir)
        session_service = InMemorySessionService()
        runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

        user_id = "demo_user"

        # Test queries demonstrating different tools
        demo_queries = [
            "Read the content of test.txt",
            "Add a new line 'Line 4' to test.txt",
            "Search for 'test' in all files in the current directory",
            "Find all .txt files in the current directory",
        ]

        for query in demo_queries:
            current_session_id = str(uuid.uuid4())

            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=current_session_id,
            )

            print(f"🆔 Session ID: {current_session_id[:8]}...")
            print(f"📝 User: {query}")

            user_content = Content(parts=[Part.from_text(text=query)])

            print("🤖 Assistant: ", end="", flush=True)
            async for event in runner.run_async(user_id=user_id,
                                                session_id=current_session_id,
                                                new_message=user_content):
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
                    # Uncomment to get the full text output of the LLM
                    # elif part.text:
                    #     print(f"\n✅ {part.text}")

            print("\n" + "-" * 40)

        await runner.close()

        # Show final file contents
        print("\n📄 Final file contents:")
        print("\n--- test.txt ---")
        with open(test_file, "r") as f:
            print(f.read())

        print("\n✅ File Tools demonstration completed!")

    finally:
        # Cleanup: remove working directory immediately after run
        print(f"\n🧹 Cleaning up working directory: {work_dir}")
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_file_tools_agent())
