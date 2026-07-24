# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay harness:数据模型 + replay_case 驱动。

``replay_case(backend, case)`` 把一条 ReplayOp 序列翻译成对 SessionService /
MemoryService 的调用,末尾读取后端中立快照。确定性 Event(id/timestamp 固定)+
确定性 summarizer 保证跨后端可比。
"""

from __future__ import annotations

import time
from typing import Any
from typing import Literal
from typing import Optional
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import Part

if TYPE_CHECKING:
    from trpc_agent_sdk.abc import MemoryServiceABC
    from trpc_agent_sdk.abc import SessionServiceABC

# 非业务字段的归一化占位符(保留键的存在性,优于 pop 删除)。
NORMALIZED = "<normalized>"

OpType = Literal[
    "create_session",
    "append_event",
    "function_call",
    "function_response",
    "update_state",
    "memory_store",
    "memory_search",
    "create_summary",
    "update_summary",
    "fail_before_commit",
    "retry_event",
]


class ReplayOp(BaseModel):
    """单步操作。各 op 类型按需读取字段子集;flat 结构保证 jsonl 可读。"""

    model_config = ConfigDict(extra="forbid")

    op: OpType
    app_name: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    session_ref: Optional[str] = None
    author: Optional[str] = None
    text: Optional[str] = None
    state_delta: Optional[dict[str, Any]] = None
    function_name: Optional[str] = None
    function_args: Optional[dict[str, Any]] = None
    function_response: Optional[Any] = None
    function_response_id: Optional[str] = None
    event_id: Optional[str] = None
    invocation_id: Optional[str] = None
    timestamp: Optional[float] = None
    memory_key: Optional[str] = None
    memory_query: Optional[str] = None
    summary_text: Optional[str] = None
    fail: bool = False


class AllowedDiffRule(BaseModel):
    """允许的差异规则:JSONPath 精确匹配 + 强制 reason。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    reason: str
    backend_pair: Optional[tuple[str, str]] = None


class ReplayCase(BaseModel):
    """一条标准回放轨迹。10 条 jsonl 均为正常一致性轨迹;
    人为不一致由 injectors 在运行时程序化派生,不写进 case 文件。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    description: str
    operations: list[ReplayOp] = Field(default_factory=list)
    allowed_diff: list[AllowedDiffRule] = Field(default_factory=list)


class ReplayBackend:
    """一个被测后端:持有 session/memory service 实例。"""

    def __init__(
        self,
        name: str,
        session_service: "SessionServiceABC",
        memory_service: "Optional[MemoryServiceABC]" = None,
    ) -> None:
        self.name = name
        self.session_service = session_service
        self.memory_service = memory_service


class ReplaySnapshot(BaseModel):
    """后端中立快照。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    case_id: str = ""
    backend_name: str = ""
    session_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    historical_events: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 驱动逻辑
# ---------------------------------------------------------------------------


def _build_event(op: ReplayOp) -> Event:
    """按 ReplayOp 构造确定性 Event。retry_event 按 text 走 append 语义。"""
    parts: list[Part] = []
    if op.op == "function_call":
        parts.append(Part.from_function_call(name=op.function_name or "f", args=op.function_args or {}))
    elif op.op == "function_response":
        parts.append(Part.from_function_response(name=op.function_name or "f", response=op.function_response or {}))
    elif op.text is not None:
        parts.append(Part.from_text(text=op.text))

    role = "user" if op.author == "user" else "model"
    kwargs: dict[str, Any] = {
        "invocation_id": op.invocation_id or "replay",
        "author": op.author or "user",
    }
    if op.event_id is not None:
        kwargs["id"] = op.event_id
    if op.timestamp is not None:
        kwargs["timestamp"] = op.timestamp
    if parts:
        kwargs["content"] = Content(role=role, parts=parts)
    if op.state_delta:
        kwargs["actions"] = EventActions(state_delta=op.state_delta)
    return Event(**kwargs)


async def replay_case(backend: ReplayBackend, case: ReplayCase) -> ReplaySnapshot:
    """顺序执行 operations,采集后端中立快照。"""
    svc = backend.session_service
    mem = backend.memory_service
    mgr = svc.summarizer_manager
    sessions: dict[str, Session] = {}
    main: Optional[Session] = None
    summary_version = 0
    event_seq = 0
    base_ts = time.time()
    memory_results: dict[str, Any] = {}

    for op in case.operations:
        if op.op == "create_session":
            session = await svc.create_session(
                app_name=op.app_name or "replay",
                user_id=op.user_id or "u",
                state=op.state_delta,
                session_id=op.session_id,
            )
            sessions[op.session_id or session.id] = session
            if main is None:
                main = session
            continue

        if op.op == "fail_before_commit":
            # 模拟中途失败:跳过这步(不 append),由后续 retry_event 重做。
            continue

        target = sessions.get(op.session_id or op.session_ref or "") or main
        if target is None:
            continue

        if op.op in ("append_event", "function_call", "function_response", "update_state", "retry_event"):
            event_seq += 1
            event = _build_event(op)
            if op.timestamp is None:
                # 基于 base_ts 递增 1s:既避开 SQLite PreciseTimestamp 精度丢失致 events 排序乱,
                # 又保持合理量级(Windows datetime.timestamp() 对过小值抛 OSError)。
                event = event.model_copy(update={"timestamp": base_ts + event_seq})
            await svc.append_event(target, event)
        elif op.op == "memory_store":
            if mem is not None:
                await mem.store_session(target)
        elif op.op == "memory_search":
            if mem is not None:
                resp = await mem.search_memory(key=target.save_key, query=op.memory_query or "")
                memory_results[op.memory_query or "q"] = [m.model_dump() for m in resp.memories]
        elif op.op in ("create_summary", "update_summary"):
            if mgr is not None:
                await mgr.create_session_summary(target, force=True)
                summary_version += 1
                await svc.update_session(target)

    if main is None:
        return ReplaySnapshot(case_id=case.case_id, backend_name=backend.name, session_id="")

    got = await svc.get_session(app_name=main.app_name, user_id=main.user_id, session_id=main.id) or main

    summary_out: dict[str, Any] = {}
    if mgr is not None:
        summ = await mgr.get_session_summary(got)
        if summ is not None:
            summary_out = {
                "current": {
                    "text": summ.summary_text,
                    "version": summary_version,
                    "session_id": summ.session_id,
                    "original_event_count": summ.original_event_count,
                    "compressed_event_count": summ.compressed_event_count,
                }
            }
        else:
            summary_out = {"current": None}

    return ReplaySnapshot(
        case_id=case.case_id,
        backend_name=backend.name,
        session_id=got.id,
        events=[e.model_dump() for e in got.events],
        historical_events=[e.model_dump() for e in got.historical_events],
        state=dict(got.state),
        memory=memory_results,
        summary=summary_out,
    )


def load_cases(dir_path: str) -> list[ReplayCase]:
    """从目录加载所有 ``*.jsonl`` case(每行一个 JSON 对象)。"""
    from pathlib import Path

    cases: list[ReplayCase] = []
    for p in sorted(Path(dir_path).glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cases.append(ReplayCase.model_validate_json(line))
    return cases
