# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo entry point for the TodoWriteTool example.

The script drives a multi-turn conversation in ONE session so it exercises
the most important property of ``TodoWriteTool``: the checklist lives in
session-level state and survives across ``Runner.run_async`` invocations.

After each ``todo_write`` the demo renders the current checklist (``✅`` /
``🔄`` / ``⬜``). At the end of each turn it reads the persisted list back
from the session with :func:`get_todos` to prove cross-turn persistence.

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
from trpc_agent_sdk.tools import get_todos
from trpc_agent_sdk.tools import render_todos
from trpc_agent_sdk.tools import TodoItem
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "todo_agent_demo"
USER_ID = "demo_user"

TURNS = [
    (
        "搭建静态站点",
        "请帮我在当前目录搭建一个 demo 静态站点，要求：\n"
        "1) 创建 demo/ 及子目录 css/、js/\n"
        "2) demo/index.html：title 和 h1 均为「Todo Demo」\n"
        "3) 生成 demo/README.md 文件，内容为「Todo Demo」\n"
    ),
    (
        "优化静态站点",
        "请优化静态站点，引入 css/style.css 与 js/app.js，要求：\n"
        "1) demo/css/style.css：body 居中、浅灰背景、无衬线字体\n"
        "2) demo/js/app.js：DOMContentLoaded 时在 console 打印「Todo HITL demo loaded」\n"
        "3) 更新 demo/README.md 文件，内容为「Todo Demo with css and js」\n"
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
        case "todo_write":
            old = resp.get("oldTodos") or []
            return f"items={len(resp.get('todos') or [])} old_items={len(old)}", message
        case _:
            return str(resp), message


def _print_todo_checklist(resp: dict, *, indent: str = "   ") -> None:
    """Render todo_write response as a checklist (same format as end-of-turn summary)."""
    raw = resp.get("todos") or []
    if not raw:
        print(f"{indent}(empty)")
        return
    try:
        items = [TodoItem.model_validate(t) for t in raw]
    except Exception:
        return
    print("📋 Current checklist:")
    for line in render_todos(items).splitlines():
        print(f"{indent}{line}")


def _print_event_parts(event, *, final_text_parts: list[str]) -> None:
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
            if name == "todo_write" and isinstance(resp, dict) and "error" not in resp:
                _print_todo_checklist(resp)
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
        _print_event_parts(event, final_text_parts=final_text_parts)

    if final_text_parts:
        print(f"🤖 Assistant: {''.join(final_text_parts)}")

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
    from agent.agent import create_todo_agent

    work_dir = os.getcwd()
    demo_dir = os.path.join(work_dir, "demo")
    if os.path.isdir(demo_dir):
        shutil.rmtree(demo_dir)
        print(f"🧹 Cleaned previous {demo_dir}")

    todo_agent = create_todo_agent(work_dir=work_dir)
    runner = Runner(
        app_name=APP_NAME,
        agent=todo_agent,
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
        await _run_turn(runner, todo_agent, session_id=session_id, label=label, query=query)

    await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
