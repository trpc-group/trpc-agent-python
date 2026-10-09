#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the TencentDB Agent Memory cross-session example."""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.memory.tencentdb_memory_service import (
    TencentDBMemoryService,
    TencentDBMemoryServiceConfig,
)
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()
sys.path.append(str(Path(__file__).parent))

_FINAL_DATE_MARKERS = ("11月6", "11/6", "2026-11-06")
_FINAL_ROOM_MARKER = "3b"


def _office_messages(run_marker: str) -> tuple[str, ...]:
    return (
        f"{run_marker} 的 Q4 发版评审会原定于 11 月 5 日下午 3 点，会议室 3A。",
        "我这周负责修 pay-service 的订单超时 bug，已经在 TAPD 建了单 TAPD-88231。",
        "以后给我写 commit message 都用英文，标题不超过 72 字符，正文用 bullet 列改动。",
        "刚定位到根因是连接池 maxIdle 配成 2 太小，改成 20 后本地复现不出来了。",
        f"{run_marker} 的评审会最终改到 11 月 6 日上午 10 点，会议室 3B；"
        "原定的 11 月 5 日下午 3 点、会议室 3A 作废。",
        "发版前必须跑一遍全量回归，这是我定的硬规矩，别跳过。",
    )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


def create_memory_service() -> TencentDBMemoryService:
    """Create the V3 gateway-backed memory service."""
    config = TencentDBMemoryServiceConfig(
        enabled=True,
        endpoint=os.getenv(
            "TENCENTDB_MEMORY_ENDPOINT",
            "http://127.0.0.1:8420",
        ),
        api_key=os.getenv("TENCENTDB_MEMORY_API_KEY", "local"),
        service_id=_required_env("TENCENTDB_MEMORY_SERVICE_ID"),
        team_id=_required_env("TENCENTDB_MEMORY_TEAM_ID"),
        agent_id=_required_env("TENCENTDB_MEMORY_AGENT_ID"),
    )
    return TencentDBMemoryService(config)


async def _run_turn(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    text: str,
) -> None:
    print(f"\nUser ({session_id}): {text}")
    print("Assistant: ", end="", flush=True)
    message = Content(parts=[Part.from_text(text=text)])
    async for event in runner.run_async(
            agent_context=AgentContext(),
            user_id=user_id,
            session_id=session_id,
            new_message=message,
    ):
        if event.partial and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
    print()


async def _print_recall_verification(
    memory_service: TencentDBMemoryService,
    *,
    user_id: str,
    run_marker: str,
    recall_query: str,
) -> None:
    """Print the public recall result and its inferred memory layers."""
    result = await memory_service.search_memory(
        key=user_id,
        query=recall_query,
        limit=10,
    )
    found_layers: set[str] = set()
    current_run_l1_found = False
    current_run_final_found = False
    normalized_marker = "".join(run_marker.split()).lower()
    print("\nTencentDB recall verification:")
    for index, memory in enumerate(result.memories):
        if memory.author == "scenario":
            layer = "L2"
        elif memory.author == "core":
            layer = "L3"
        elif memory.author in {"user", "assistant", "message"}:
            layer = "L0"
        else:
            layer = "L1"
        found_layers.add(layer)
        text = "".join(part.text or "" for part in memory.content.parts)
        normalized = "".join(text.split()).lower()
        belongs_to_current_run = layer == "L1" and normalized_marker in normalized
        current_run_l1_found = current_run_l1_found or belongs_to_current_run
        if (belongs_to_current_run and any(marker in normalized for marker in _FINAL_DATE_MARKERS)
                and _FINAL_ROOM_MARKER in normalized):
            current_run_final_found = True
        preview = text if len(text) <= 500 else f"{text[:500]}...[truncated]"
        print(f"  [{index}] {layer}:{memory.author} {preview}")
    layers = ",".join(sorted(found_layers)) if found_layers else "none"
    print(
        "TencentDB recall verification summary: "
        f"layers={layers}, memories={len(result.memories)}, "
        f"current_run_l1_found={current_run_l1_found}, "
        f"current_run_final_found={current_run_final_found}", )


async def main() -> None:
    """Write office facts, wait for extraction, then recall across sessions."""
    from agent.agent import create_agent
    from agent.agent import root_agent

    memory_service = create_memory_service()
    session_service = InMemorySessionService()
    write_runner = Runner(
        app_name="tencentdb_memory_demo",
        agent=create_agent(recall_enabled=False),
        session_service=session_service,
        memory_service=memory_service,
        close_session_service_on_close=False,
        close_memory_service_on_close=False,
    )
    recall_runner = Runner(
        app_name="tencentdb_memory_demo",
        agent=root_agent,
        session_service=session_service,
        memory_service=memory_service,
    )
    user_id = _required_env("TENCENTDB_MEMORY_USER_ID")
    run_id = uuid4().hex[:8]
    run_marker = f"验证项目-{run_id}"
    recall_query = f"{run_marker} 的 Q4 发版评审会最终安排在什么时间和会议室？"
    write_session_id = f"office-write-{run_id}"
    recall_session_id = f"office-recall-{run_id}"
    print(f"TencentDB verification marker: {run_marker}")

    try:
        for text in _office_messages(run_marker):
            await _run_turn(
                write_runner,
                user_id=user_id,
                session_id=write_session_id,
                text=text,
            )

        wait_seconds = float(os.getenv("TENCENTDB_MEMORY_PIPELINE_WAIT_SECONDS", "10"), )
        print(f"Waiting {wait_seconds:g}s for asynchronous memory extraction...", )
        await asyncio.sleep(wait_seconds)

        await _print_recall_verification(
            memory_service,
            user_id=user_id,
            run_marker=run_marker,
            recall_query=recall_query,
        )

        await _run_turn(
            recall_runner,
            user_id=user_id,
            session_id=recall_session_id,
            text=recall_query,
        )
    finally:
        await write_runner.close()
        await recall_runner.close()


if __name__ == "__main__":
    asyncio.run(main())
