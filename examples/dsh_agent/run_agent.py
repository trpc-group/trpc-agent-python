# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the DSHAgent example with the gateway configured in .env."""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "dsh_agent_demo"
USERS = (
    ("alice", "42"),
    ("bob", "7"),
)


async def _run_query(runner: Runner, user_id: str, session_id: str, query: str) -> None:
    print(f"\n📝 User: {query}")
    print("🤖 Assistant: ", end="", flush=True)

    saw_partial = False
    final_text: list[str] = []
    async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=Content(parts=[Part(text=query)]),
    ):
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            saw_partial = True
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.function_call:
                print(f"\n🔧 [Tool Call: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                print(f"📊 [Tool Result: {part.function_response.response}]")
            elif part.text and not part.thought:
                final_text.append(part.text)

    if not saw_partial and final_text:
        print("".join(final_text), end="", flush=True)
    print()


async def _run_user(
    runner: Runner,
    session_service: InMemorySessionService,
    user_id: str,
    file_content: str,
) -> None:
    from agent.agent import workspace_for

    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    print(f"\n===== user: {user_id} =====")
    print(f"🆔 Session ID: {session_id[:8]}...")

    await _run_query(
        runner,
        user_id,
        session_id,
        f"请在当前工作目录下创建 hello.txt，文件内容只写 {file_content}。完成后只回复 done。",
    )
    await _run_query(runner, user_id, session_id, "读取 hello.txt，只回答文件里的数字。")

    workspace = workspace_for(user_id)
    target = workspace / "hello.txt"
    print(f"\n📁 User workspace: {workspace}")
    if target.is_file():
        print(f"✅ hello.txt = {target.read_text(encoding='utf-8').strip()}")
    else:
        print("❌ hello.txt was not created")


async def run_dsh_agent() -> None:
    """Run two users; each one gets an isolated DSH workspace."""

    from agent.agent import root_agent

    await root_agent.init()
    try:
        session_service = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

        for user_id, file_content in USERS:
            await _run_user(runner, session_service, user_id, file_content)
    finally:
        await root_agent.destroy()


if __name__ == "__main__":
    asyncio.run(run_dsh_agent())
