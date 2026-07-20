# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demo entry point for the Goal tools example.

Runs two independent test cases back-to-back, each using a fresh session:

  Case 1 – "模型自己设置 Goal":
    The user sends a plain request; the agent decides to call ``create_goal``
    itself, executes the work step by step, and calls ``update_goal('complete')``
    when done.  The enforcement callbacks guard every turn: any premature
    final response while the goal is still active is intercepted, a nudge is
    injected, and the agent loop retries within the same invocation.

  Case 2 – "用户（宿主）设置 Goal":
    The host application calls ``start_goal()`` before the first turn — the
    goal is written directly into ``session.state``.  The agent never calls
    ``create_goal``; the enforcement callbacks pick up the pre-existing active
    goal from the very first token.  The agent only has to do the work and
    call ``update_goal('complete')`` when finished.

Set TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME (see .env)
before running.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools.goal_tools import get_goal_record
from trpc_agent_sdk.tools.goal_tools import render_goal
from trpc_agent_sdk.tools.goal_tools import start_goal
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "goal_agent_demo"
USER_ID = "demo_user"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _summarise_tool_response(name: str, resp: object) -> str:
    if not isinstance(resp, dict):
        return str(resp)
    if "error" in resp:
        return f"error={resp['error']!r}"
    match name:
        case "Bash":
            stdout = (resp.get("stdout") or "").strip()
            stdout = stdout[:120] + ("..." if len(stdout) > 120 else "")
            return f"success={resp.get('success')} rc={resp.get('return_code')} stdout={stdout!r}"
        case "Write":
            return f"path={resp.get('path')!r} success={resp.get('success')}"
        case "Read":
            return f"path={resp.get('path')!r} lines={resp.get('total_lines')}"
        case "create_goal":
            g = resp.get("goal") or {}
            return f"created id={g.get('id')} status={g.get('status')} objective={g.get('objective')!r}"
        case "update_goal":
            g = resp.get("goal") or {}
            return f"updated status={g.get('status')}"
        case "get_goal":
            g = resp.get("goal") or {}
            return f"status={g.get('status')} objective={g.get('objective')!r}" if g else "(no goal)"
        case _:
            return str(resp)


async def _run_turn(
    runner: Runner,
    *,
    session_id: str,
    label: str,
    query: str,
    agent_name: str,
) -> None:
    """Drive a single user turn, pretty-print events, then show the persisted goal."""
    print(f"\n{'=' * 56}")
    print(f"  Turn: {label}")
    print(f"{'=' * 56}")
    print(f"📝 User: {query}\n")

    # Collect text separately from partial and non-partial events.
    # Tool calls/responses arrive as non-partial events and are printed immediately.
    # Model text may arrive as streaming partial chunks; we fall back to partial
    # collection so pure-text responses are always visible.
    streaming_text: list[str] = []  # accumulated from partial text chunks
    final_text: list[str] = []  # text from the last non-partial event

    user_content = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=user_content,
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.thought:
                continue
            if part.function_call:
                if not event.partial:
                    print(f"\n🔧 [Tool call]   {part.function_call.name}({part.function_call.args})")
            elif part.function_response:
                if not event.partial:
                    name = part.function_response.name
                    resp = part.function_response.response
                    summary = _summarise_tool_response(name, resp)
                    print(f"\n📊 [Tool result] {summary}")
                    if isinstance(resp, dict) and resp.get("message"):
                        print(f"\n💬 {resp['message']}")
            elif part.text:
                if event.partial:
                    streaming_text.append(part.text)
                else:
                    final_text.append(part.text)

    # Prefer consolidated non-partial text; fall back to streaming chunks.
    text_to_show = "".join(final_text) or "".join(streaming_text)
    if text_to_show:
        print(f"\n🤖 Assistant: {text_to_show}")

    session = await runner.session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    goal = get_goal_record(session, branch=agent_name)
    print(f"\n{'-' * 40}")
    print("🎯 Persisted goal (read from session):")
    print(render_goal(goal))
    print("-" * 40)


def _clean(work_dir: str, *names: str) -> None:
    for name in names:
        path = os.path.join(work_dir, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"🧹 Cleaned {path}")
        elif os.path.isfile(path):
            os.remove(path)
            print(f"🧹 Cleaned {path}")


# ---------------------------------------------------------------------------
# Case 1: 模型自己设置 Goal
# ---------------------------------------------------------------------------

CASE1_TURNS = [
    (
        "搭建 Python 工具包（模型设置目标）",
        # 多步骤文件操作任务：需要创建目录 + 多个文件 + 验证，
        # 足够复杂，观测模型是否自主调用 create_goal 再逐步执行。
        "基于goal能力，请在当前目录搭建一个最小 Python 工具包 mypkg/，需要完成：\n"
        "1) 创建 mypkg/__init__.py，内容：__version__ = '0.1.0'\n"
        "2) 创建 mypkg/utils.py，包含函数 greet(name: str) -> str\n"
        "3) 创建 mypkg/README.md，标题「mypkg」，一句话描述该工具包的功能\n"
        "请逐步执行并在完成后验证三个文件都已存在。",
    ),
]


async def case1_model_sets_goal(work_dir: str) -> None:
    """Case 1: the LLM calls create_goal autonomously."""
    from agent.agent import create_goal_agent

    print("\n" + "-" * 56)
    print("  Case 1: 模型自己设置 Goal（agent 调用 create_goal）")
    print("-" * 56)

    _clean(work_dir, "mypkg", "README.md")

    agent = create_goal_agent(work_dir=work_dir)
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=InMemorySessionService(),
    )

    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    print(f"🆔 Session: {session_id[:8]}...")

    for label, query in CASE1_TURNS:
        await _run_turn(runner, session_id=session_id, label=label, query=query, agent_name=agent.name)

    await runner.close()


# ---------------------------------------------------------------------------
# Case 2: 用户（宿主）设置 Goal
# ---------------------------------------------------------------------------

CASE2_OBJECTIVE = (
    # 目标与 prompt 差异化，观察模型是否能够正确理解目标并执行任务
    "在当前目录创建 notes/ 目录，其中包含两个文件：\n"
    "  - summary.txt：用三句话描述 Python 异步编程的核心概念\n"
    "  - example.py：一个可运行的 asyncio 示例（包含 main 协程和 asyncio.run 调用）")

CASE2_TURNS = [(
    "执行任务（用户设置目标）",
    # 宿主已通过 start_goal() 设置了目标，消息里不提及任何 goal 工具。
    "请在当前目录创建 notes/ 目录，在其中写文件：\n"
    "summary.txt：用三句话描述 Python 异步编程的核心概念\n")]


async def case2_user_sets_goal(work_dir: str) -> None:
    """Case 2: the host calls start_goal() before the first turn.

    The goal is written into session.state directly; the agent never calls
    create_goal, but the enforcement callbacks are fully active.
    """
    from agent.agent import create_goal_agent

    print("\n" + "-" * 56)
    print("  Case 2: 用户（宿主）设置 Goal（调用 start_goal()）")
    print("-" * 56)

    _clean(work_dir, "notes")

    _clean(work_dir, "poem")

    agent = create_goal_agent(work_dir=work_dir)
    session_service = InMemorySessionService()
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=session_service,
    )

    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )

    # ── 应用层直接设置目标，模型无需调用 create_goal ──────────────────────── #
    goal = await start_goal(
        session_service,
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
        objective=CASE2_OBJECTIVE,
        agent_name=agent.name,
    )
    print(f"🆔 Session: {session_id[:8]}...")
    print(f"🎯 Goal pre-injected by host:")
    print(f"   objective: {goal.objective!r}")
    print(f"   status:    {goal.status.value}")
    print("\n📌 Note: goal is active from the first token.\n"
          "   The agent does NOT call create_goal.\n")

    for label, query in CASE2_TURNS:
        await _run_turn(runner, session_id=session_id, label=label, query=query, agent_name=agent.name)

    await runner.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    work_dir = os.getcwd()
    await case1_model_sets_goal(work_dir)
    await case2_user_sets_goal(work_dir)


if __name__ == "__main__":
    asyncio.run(main())
