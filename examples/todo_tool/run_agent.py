# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo entry point for the TodoWriteTool example.

The script drives a single multi-turn conversation in ONE session so it
exercises the most important property of ``TodoWriteTool``: the checklist
lives in session-level state and survives across ``Runner.run_async``
invocations (turns), so the agent picks up exactly where it left off.

Flow:

1. Turn 1 — ask the agent to plan a 3-step task. It calls ``todo_write``
   once to lay out the full list with the first item ``in_progress``.
2. Turns 2-4 — tell the agent each step is done. On every turn it flips
   the finished item to ``completed`` and the next one to ``in_progress``
   in a single ``todo_write`` call.

After each turn the demo reads the PERSISTED list back from the session
with :func:`get_todos` and renders it as an ASCII checklist, proving the
state round-trips across runs (``[x]`` done, ``[>]`` in progress,
``[ ]`` pending).

Set TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME (see
.env) before running.
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import get_todos
from trpc_agent_sdk.tools import render_todos
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "todo_agent_demo"
USER_ID = "demo_user"

# Turns are sent sequentially into the SAME session so each one resumes
# from the checklist persisted by the previous turn.
TURNS = [
    ("规划任务",
     "请规划一个三步任务并用 todo_write 记录：1) 初始化项目骨架 2) 实现核心业务逻辑 3) 编写并跑通单元测试。"
     "先整体规划，把第一步设为进行中，其余为待办。"),
    ("完成第 1 步", "第一步『初始化项目骨架』已经完成了，请更新清单并开始下一步。"),
    ("完成第 2 步", "第二步『实现核心业务逻辑』也完成了，请更新清单并继续。"),
    ("完成第 3 步", "最后一步『编写并跑通单元测试』完成了，请把清单更新到全部完成。"),
]


def _summarise_tool_response(resp) -> tuple[str, str | None]:
    """Compact the ``todo_write`` response; also return ``message`` for nudge visibility."""
    if not isinstance(resp, dict):
        return str(resp), None
    if "error" in resp:
        return f"error={resp.get('error')!r}", None
    todos = resp.get("todos") or []
    old = resp.get("oldTodos")
    statuses = ", ".join(f"{t.get('status')}:{t.get('content')}" for t in todos)
    summary = f"items={len(todos)} old_items={len(old) if old else 0} [{statuses}]"
    message = resp.get("message")
    return summary, message if isinstance(message, str) and message else None


async def _run_turn(runner: Runner, agent: LlmAgent, *, session_id: str, label: str, query: str) -> None:
    """Drive a single user turn through ``runner`` and pretty-print events."""
    print(f"\n========== {label} ==========")
    print(f"📝 User: {query}")

    final_text_parts: list[str] = []
    user_content = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=user_content,
    ):
        if not event.content or not event.content.parts:
            continue
        # Skip partial stream chunks — some models emit English chain-of-thought
        # there; we only print the final, non-partial assistant text below.
        if event.partial:
            continue
        for part in event.content.parts:
            if part.thought:
                continue
            if part.function_call:
                print(f"🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                summary, message = _summarise_tool_response(part.function_response.response)
                print(f"📊 [Tool Result: {summary}]")
                if message:
                    print(f"💬 [Tool Message: {message}]")
            elif part.text:
                final_text_parts.append(part.text)

    if final_text_parts:
        print(f"🤖 Assistant: {''.join(final_text_parts)}")

    # Read the PERSISTED checklist back from the session to prove the
    # state survived this run. The tool keys state by branch, falling back
    # to the agent name when the root invocation has no branch.
    session = await runner.session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    todos = get_todos(session, branch=agent.name) or get_todos(session, branch="")
    print("\n📋 Persisted checklist:")
    print(render_todos(todos) if todos else "  (empty)")
    print("-" * 40)


async def main() -> None:
    from agent.agent import todo_agent

    runner = Runner(
        app_name=APP_NAME,
        agent=todo_agent,
        session_service=InMemorySessionService(),
    )

    # One shared session for the whole conversation → cross-turn persistence.
    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"user_name": USER_ID},
    )
    print(f"🆔 Session ID: {session_id[:8]}... (shared across all turns)")

    for label, query in TURNS:
        await _run_turn(runner, todo_agent, session_id=session_id, label=label, query=query)


if __name__ == "__main__":
    asyncio.run(main())
