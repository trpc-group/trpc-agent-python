# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo entry point for TodoWriteTool with Human-in-the-Loop approval.

The script drives a multi-turn conversation in ONE session so it exercises:

- ``request_todo_plan_approval`` (``LongRunningFunctionTool``) pauses the
  agent until a human approves the initial plan.
- ``todo_write`` persists the checklist in session-level state across turns.
- ``Bash`` / ``Write`` / ``Read`` execute and verify file operations.

Flow (4 turns):

1. Turn 1 — scaffold a multi-file static site under ``demo/``; plan goes
   through HITL approval first, then the agent executes step by step.
2. Turn 2 — add an interactive button + live clock; verify with ``Read``.
3. Turn 3 — rename the heading and expand ``README.md``.
4. Turn 4 — run ``ls`` / ``find`` sanity checks and confirm all todos done.

After each turn the demo reads the PERSISTED list back from the session
with :func:`get_todos` and renders it as an ASCII checklist.

Set TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME (see
.env) before running.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import get_todos
from trpc_agent_sdk.tools import render_todos
from trpc_agent_sdk.tools import TodoItem
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "todo_hitl_demo"
USER_ID = "demo_user"

TURNS = [
    (
        "搭建静态站点",
        "请帮我在当前目录搭建一个 demo 静态站点，要求：\n"
        "1) 创建 demo/ 及子目录 css/、js/\n"
        "2) demo/index.html：title 和 h1 均为「Todo HITL Demo」，引入 css/style.css 与 js/app.js\n"
        "3) demo/css/style.css：body 居中、浅灰背景、无衬线字体\n"
        "4) demo/js/app.js：DOMContentLoaded 时在 console 打印「Todo HITL demo loaded」\n"
        "请先提交完整计划等我审批；审批通过后逐步执行",
    )
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
        case "request_todo_plan_approval":
            todos = resp.get("todos") or []
            preview = resp.get("preview") or ""
            return f"pending_approval items={len(todos)} preview={preview!r}", message
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


async def _consume_run(
    runner: Runner,
    *,
    session_id: str,
    content: Content,
) -> tuple[list[str], Optional[LongRunningEvent]]:
    """Run one ``runner.run_async`` invocation; capture text and HITL events."""
    final_text_parts: list[str] = []
    long_running_event: Optional[LongRunningEvent] = None

    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=content,
    ):
        if isinstance(event, LongRunningEvent):
            long_running_event = event
            resp = event.function_response.response
            print("\n🔄 [Long-running operation detected — waiting for human approval]")
            print(f"   Function: {event.function_call.name}")
            print(f"   Args: {event.function_call.args}")
            if isinstance(resp, dict) and resp.get("preview"):
                print("   Proposed checklist:")
                for line in str(resp["preview"]).splitlines():
                    print(f"     {line}")
            continue
        _print_event_parts(event, final_text_parts=final_text_parts)

    return final_text_parts, long_running_event


def _build_approval_resume(long_running_event: LongRunningEvent) -> Content:
    """Simulate human approval with a plan edit: inject an extra todo."""
    response_data = dict(long_running_event.function_response.response)
    if response_data.get("status") != "pending_approval":
        raise ValueError(f"Expected pending_approval, got {response_data.get('status')!r}")

    todos = list(response_data.get("todos") or [])
    todos.append({
        "content": "生成 README 文件",
        "activeForm": "正在生成 README 文件",
        "status": "pending",
    })
    response_data["todos"] = todos
    response_data["preview"] = render_todos([TodoItem.model_validate(t) for t in todos])

    response_data["status"] = "approved"
    response_data["message"] = (
        "APPROVED with edit: added todo「生成 README 文件」— proceed with todo_write using the updated list."
    )
    response_data["approved_by"] = USER_ID
    response_data["timestamp"] = time.time()

    print("\n👤 [Human approval with plan edit]")
    print(f"   Decision: approved by {response_data['approved_by']}")
    print("   Edit: added todo → 生成 README 文件")
    print("   Updated checklist:")
    for line in str(response_data["preview"]).splitlines():
        print(f"     {line}")

    resume_function_response = FunctionResponse(
        id=long_running_event.function_response.id,
        name=long_running_event.function_response.name,
        response=response_data,
    )
    return Content(role="user", parts=[Part(function_response=resume_function_response)])


async def _run_turn(
    runner: Runner,
    agent: LlmAgent,
    *,
    session_id: str,
    label: str,
    query: str,
) -> None:
    """Drive a user turn, including optional HITL resume within the same turn."""
    print(f"\n========== {label} ==========")
    print(f"📝 User: {query}")

    user_content = Content(parts=[Part.from_text(text=query)])
    final_text_parts, long_running_event = await _consume_run(
        runner,
        session_id=session_id,
        content=user_content,
    )

    if long_running_event:
        resume_content = _build_approval_resume(long_running_event)
        print("\n🔄 Resuming agent after human approval...")
        resume_text_parts, _ = await _consume_run(
            runner,
            session_id=session_id,
            content=resume_content,
        )
        final_text_parts.extend(resume_text_parts)

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
