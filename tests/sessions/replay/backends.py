# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""三后端实例化 + env 门控 + 确定性 summarizer。

三后端**显式传同一个 SessionServiceConfig**(否则 InMemory 默认 store_hist=False、
SQL/Redis 默认 True,会产生历史事件不一致)。memory 三后端 enabled=True。
summary 用同一 DeterministicSummarizer 挂到每个 service。
"""

from __future__ import annotations

import os
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions._summarizer_manager import SummarizerSessionManager

from .harness import ReplayBackend
from .report import BackendStatus


class DeterministicSummarizer(SessionSummarizer):
    """覆写唯一调 LLM 的 ``_compress_session_to_summary``,返回确定性文本。"""

    def __init__(self) -> None:
        # model 仅占位;覆写后 _generate_summary 永不被调用。
        super().__init__(model=None)  # type: ignore[arg-type]

    async def _compress_session_to_summary(
        self,
        events: list[Event],
        session_id: str,
        ctx: Optional[InvocationContext] = None,
    ) -> Optional[str]:
        texts: list[str] = []
        for ev in events:
            text = ev.get_text() if hasattr(ev, "get_text") else None
            if text:
                texts.append(f"[{ev.author}] {text}")
        return "DETERMINISTIC SUMMARY: " + " | ".join(texts) if texts else None


def _session_config() -> SessionServiceConfig:
    cfg = SessionServiceConfig(store_historical_events=True)
    cfg.clean_ttl_config()
    return cfg


def _manager() -> SummarizerSessionManager:
    return SummarizerSessionManager(model=None, summarizer=DeterministicSummarizer())  # type: ignore[arg-type]


def in_memory_backend() -> ReplayBackend:
    svc = InMemorySessionService(summarizer_manager=_manager(), session_config=_session_config())
    mem = InMemoryMemoryService(enabled=True)
    return ReplayBackend("in_memory", svc, mem)


def sqlite_backend(db_url: str = "sqlite:///:memory:") -> ReplayBackend:
    svc = SqlSessionService(db_url=db_url, summarizer_manager=_manager(), session_config=_session_config())
    mem = SqlMemoryService(db_url=db_url, enabled=True)
    return ReplayBackend("sqlite", svc, mem)


def redis_backend(url: str) -> ReplayBackend:
    svc = RedisSessionService(db_url=url, summarizer_manager=_manager(), session_config=_session_config(), is_async=True)
    mem = RedisMemoryService(db_url=url, enabled=True, is_async=True)
    return ReplayBackend("redis", svc, mem)


def enabled_backends(tmp_path: Optional[str] = None, ) -> tuple[list[ReplayBackend], list[BackendStatus]]:
    """按环境变量返回启用的后端 + 各自状态。轻量模式默认 in_memory + sqlite。"""
    backends = [in_memory_backend()]
    statuses = [BackendStatus(name="in_memory", status="match")]

    sql_url = os.environ.get("TRPC_REPLAY_SQL_URL")
    if not sql_url and tmp_path:
        sql_url = f"sqlite:///{tmp_path}/replay.db"
    if not sql_url:
        sql_url = "sqlite:///:memory:"
    try:
        backends.append(sqlite_backend(sql_url))
        statuses.append(BackendStatus(name="sqlite", status="match"))
    except Exception as exc:  # noqa: BLE001
        statuses.append(BackendStatus(name="sqlite", status="skipped", reason=str(exc)))

    redis_url = os.environ.get("TRPC_REPLAY_REDIS_URL")
    if redis_url:
        try:
            backends.append(redis_backend(redis_url))
            statuses.append(BackendStatus(name="redis", status="match"))
        except Exception as exc:  # noqa: BLE001
            statuses.append(BackendStatus(name="redis", status="skipped", reason=str(exc)))
    else:
        statuses.append(BackendStatus(name="redis", status="skipped", reason="TRPC_REPLAY_REDIS_URL unset"))

    return backends, statuses
