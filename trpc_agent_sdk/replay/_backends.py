# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay backend construction helpers."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SummarizerSessionManager

from ._model import ReplaySummaryModel
from ._summarizer import ReplaySessionSummarizer


class ReplayBackend:
    """A paired SessionService and MemoryService backend."""

    def __init__(
        self,
        name: str,
        session_service,
        memory_service,
        model: ReplaySummaryModel,
        summarizer: ReplaySessionSummarizer,
        manager: SummarizerSessionManager,
    ) -> None:
        self.name = name
        self.session_service = session_service
        self.memory_service = memory_service
        self.model = model
        self.summarizer = summarizer
        self.manager = manager

    async def close(self) -> None:
        """Release both backend services."""
        try:
            await self.memory_service.close()
        finally:
            await self.session_service.close()


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


async def _build_backend(name: str, work_dir: Path, environ: Mapping[str, str]) -> ReplayBackend:
    model = ReplaySummaryModel()
    summarizer = ReplaySessionSummarizer(model)
    manager = SummarizerSessionManager(model=model, summarizer=summarizer)

    if name == "inmemory":
        session_service = InMemorySessionService(
            summarizer_manager=manager,
            session_config=_session_config(),
        )
        memory_service = InMemoryMemoryService(memory_service_config=_memory_config())
    elif name == "sqlite":
        sqlite_path = work_dir / "replay.sqlite3"
        db_url = f"sqlite:///{sqlite_path}"
        session_service = SqlSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=_session_config(),
            is_async=False,
        )
        memory_service = SqlMemoryService(
            db_url=db_url,
            memory_service_config=_memory_config(),
            is_async=False,
        )
        await session_service._sql_storage.create_sql_engine()
        await memory_service._sql_storage.create_sql_engine()
    elif name == "sql":
        db_url = environ.get("TRPC_REPLAY_SQL_URL")
        if not db_url:
            raise ValueError("TRPC_REPLAY_SQL_URL is required when the sql backend is selected")
        session_service = SqlSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=_session_config(),
            is_async=db_url.startswith(("postgresql+", "mysql+")),
        )
        memory_service = SqlMemoryService(
            db_url=db_url,
            memory_service_config=_memory_config(),
            is_async=db_url.startswith(("postgresql+", "mysql+")),
        )
        await session_service._sql_storage.create_sql_engine()
        await memory_service._sql_storage.create_sql_engine()
    elif name == "redis":
        db_url = environ.get("TRPC_REPLAY_REDIS_URL")
        if not db_url:
            raise ValueError("TRPC_REPLAY_REDIS_URL is required when the redis backend is selected")
        session_service = RedisSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=_session_config(),
        )
        memory_service = RedisMemoryService(
            db_url=db_url,
            memory_service_config=_memory_config(),
        )
    else:
        raise ValueError(f"Unsupported replay backend: {name}")

    return ReplayBackend(name, session_service, memory_service, model, summarizer, manager)


def resolve_backend_names(environ: Mapping[str, str] = os.environ) -> list[str]:
    """Resolve lightweight and opt-in integration backends from environment."""
    configured = environ.get("TRPC_REPLAY_BACKENDS")
    if configured:
        names = [name.strip().lower() for name in configured.split(",") if name.strip()]
    else:
        names = ["inmemory", "sqlite"]
        if environ.get("TRPC_REPLAY_SQL_URL"):
            names.append("sql")
        if environ.get("TRPC_REPLAY_REDIS_URL"):
            names.append("redis")

    if not names:
        raise ValueError("At least one replay backend must be selected")
    unknown = set(names) - {"inmemory", "sqlite", "sql", "redis"}
    if unknown:
        raise ValueError(f"Unsupported replay backends: {sorted(unknown)}")
    return list(dict.fromkeys(names))