# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo entry point for the Task tools example.

The script drives a multi-turn conversation in ONE session so it exercises
the most important properties of the Task toolset: the board lives in
session-level state and survives across ``Runner.run_async`` invocations,
tasks carry server-assigned ids, and dependencies are enforced.

After each ``task_update`` that sets ``status`` to ``in_progress`` or
``completed``, the demo renders the current board (``✅`` / ``🔄`` / ``⬜``).
At the end of each turn it reads the persisted board back from the session
with :func:`get_task_store` to prove cross-turn persistence.

Set TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME (see
.env) before running.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import get_task_store
from trpc_agent_sdk.tools import render_task_list
from trpc_agent_sdk.tools.task_tools._models import TaskStore
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "task_agent_demo"
USER_ID = "demo_user"

TURNS = [
    (
        "搭建静态站点",
        "请帮我在当前目录搭建 demo 静态站点。先规划任务并逐步执行：\n"
        "1) 创建 demo/ 及子目录 css/、js/\n"
        "2) demo/index.html：title 和 h1 均为「Task Demo」\n"
        "3) demo/README.md，内容为「Task Demo」\n"
    ),
    (
        "优化静态站点",
        "请优化静态站点，引入 css/style.css 与 js/app.js。用规划任务并逐步执行：\n"
        "1) demo/css/style.css：body 居中、浅灰背景、无衬线字体\n"
        "2) demo/js/app.js：DOMContentLoaded 时在 console 打印「Task demo loaded」\n"
        "3) 更新 demo/index.html，引入 css/style.css 与 js/app.js\n"
        "4) 更新 demo/README.md, 描述 index.html的内容\n"
    ),
]


def _summarise_tool_response(name: str, resp) -> tuple[str, str | None]:
    """Compact tool responses; also return ``message`` for nudge visibility."""
    if not isinstance(resp, dict):
        return str(resp), None
    message = resp.get("message") if isinstance(resp.get("message"), str) else None
    if "error" in resp:
        return f"error={resp['error']!r}", None
    match name:
        case "Bash":
            stdout = (resp.get("stdout") or "").strip()
            stdout = stdout[:80] + ("..." if len(stdout) > 80 else "")
            return f"success={resp.get('success')} rc={resp.get('return_code')} stdout={stdout!r}", None
        case "Write":
            return f"path={resp.get('path')!r} success={resp.get('success')}", message
        case "Read":
            return f"path={resp.get('path')!r} lines={resp.get('total_lines')}", None
        case "task_create":
            task = resp.get("task") or {}
            return f"created id={task.get('id')} subject={task.get('subject')!r}", message
        case "task_update":
            task = resp.get("task") or {}
            unblocked = resp.get("unblocked") or []
            extra = f" unblocked={unblocked}" if unblocked else ""
            return f"updated id={task.get('id')} status={task.get('status')}{extra}", message
        case "task_list":
            tasks = resp.get("tasks") or []
            return f"list items={len(tasks)} stats={resp.get('stats')}", None
        case "task_get":
            task = resp.get("task") or {}
            return f"get id={task.get('id')} status={task.get('status')}", None
        case _:
            return str(resp), message


def _print_task_board(store: TaskStore, *, indent: str = "   ") -> None:
    """Render the persisted task board (same glyphs as render_task_list)."""
    if not store.tasks:
        print(f"{indent}(empty)")
        return
    print("\n📋 Current task board:")
    for line in render_task_list(store).splitlines():
        print(f"{indent}{line}")
    print("\n")

def _should_print_task_board(name: str, resp: dict) -> bool:
    """Print board after task_update sets in_progress or completed."""
    if name != "task_update" or "error" in resp:
        return False
    status = (resp.get("task") or {}).get("status")
    return status in ("in_progress", "completed")


async def _print_event_parts(
        event,
        *,
        final_text_parts: list[str],
        runner: Runner,
        session_id: str,
        agent_name: str,
) -> None:
    """Pretty-print non-partial assistant / tool events."""
    if not event.content or not event.content.parts:
        return
    if event.partial:
        return
    for part in event.content.parts:
        if part.thought:
            continue
        if part.function_call:
            print(f"🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
        elif part.function_response:
            name = part.function_response.name
            resp = part.function_response.response
            summary, message = _summarise_tool_response(name, resp)
            print(f"📊 [Tool Result: {summary}]")
            if message:
                print(f"💬 [Tool Message: {message}]")
            if isinstance(resp, dict) and _should_print_task_board(name, resp):
                session = await runner.session_service.get_session(
                    app_name=APP_NAME,
                    user_id=USER_ID,
                    session_id=session_id,
                )
                store = get_task_store(session, branch=agent_name)
                if not store.tasks:
                    store = get_task_store(session, branch="")
                _print_task_board(store)
        elif part.text:
            final_text_parts.append(part.text)


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
        await _print_event_parts(
            event,
            final_text_parts=final_text_parts,
            runner=runner,
            session_id=session_id,
            agent_name=agent.name,
        )

    if final_text_parts:
        print(f"🤖 Assistant: {''.join(final_text_parts)}")

    session = await runner.session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    store = get_task_store(session, branch=agent.name)
    if not store.tasks:
        store = get_task_store(session, branch="")
    print("\n📋 Persisted task board:")
    print(render_task_list(store) if store.tasks else "  (empty)")
    print("-" * 40)


async def main() -> None:
    from agent.agent import create_task_agent

    work_dir = os.getcwd()
    demo_dir = os.path.join(work_dir, "demo")
    if os.path.isdir(demo_dir):
        shutil.rmtree(demo_dir)
        print(f"🧹 Cleaned previous {demo_dir}")

    task_agent = create_task_agent(work_dir=work_dir)
    runner = Runner(
        app_name=APP_NAME,
        agent=task_agent,
        session_service=InMemorySessionService(),
    )

    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        state={"user_name": USER_ID},
    )
    print(f"🆔 Session ID: {session_id[:8]}... (shared across all turns)")
    print(f"📂 Work dir: {work_dir}")

    for label, query in TURNS:
        await _run_turn(runner, task_agent, session_id=session_id, label=label, query=query)

    await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
