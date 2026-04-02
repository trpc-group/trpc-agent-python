# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Run the MCP tools agent demo"""

import asyncio
import subprocess
import uuid
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


def start_local_mcp_server() -> subprocess.Popen:
    """Start local mcp_server.py process in current example directory."""
    server_file = Path(__file__).with_name("mcp_server.py")
    return subprocess.Popen(
        ["python3", str(server_file)],
        cwd=str(server_file.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop_local_mcp_server(process: subprocess.Popen) -> None:
    """Stop local MCP server process safely."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


async def run_mcp_agent():
    """Run the MCP tools agent demo"""

    app_name = "mcp_agent_demo"
    server_process = start_local_mcp_server()

    # Give the process a short warm-up window and fail fast if startup crashes.
    await asyncio.sleep(0.2)
    if server_process.poll() is not None:
        stderr = ""
        if server_process.stderr:
            stderr = server_process.stderr.read().strip()
        raise RuntimeError(f"Failed to start mcp_server.py: {stderr or 'unknown error'}")

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "What's the weather like in Beijing?",
        "Calculate 15 multiplied by 3.5",
        "How is the weather in Shanghai?",
        "What is 100 divided by 4?",
    ]

    try:
        for query in demo_queries:
            current_session_id = str(uuid.uuid4())

            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=current_session_id,
                state={
                    "user_name": f"{user_id}",
                },
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

            print("\n" + "-" * 40)
    finally:
        await runner.close()
        stop_local_mcp_server(server_process)


if __name__ == "__main__":
    asyncio.run(run_mcp_agent())
