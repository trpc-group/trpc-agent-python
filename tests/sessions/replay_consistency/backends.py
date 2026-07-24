# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Backend construction for replay consistency tests.

Provides a factory that creates session/memory backend pairs for
InMemory, SQLite, and optionally Redis backends. Also includes a
deterministic session summarizer that avoids LLM non-determinism.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import AsyncGenerator

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SummarizerSessionManager


@dataclass
class BackendBundle:
    """A session + memory service pair with a deterministic name."""

    name: str
    session_service: Any
    memory_service: Any
    close: Callable[[], Awaitable[None]]


class _FakeModel(LLMModel):
    """A model stub that is never invoked — used only for summarizer metadata."""

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"deterministic-replay-model"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: Any | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        if False:  # pragma: no cover
            yield LlmResponse()


class DeterministicSessionSummarizer(SessionSummarizer):
    """A summarizer that produces deterministic output without an LLM.

    Overrides the private compression method to build a stable summary
    string from event text and tool metadata. This eliminates LLM
    non-determinism while still exercising the full SDK compression
    pipeline (event selection, splitting, metadata tracking).
    """

    async def _compress_session_to_summary(
        self,
        events: list[Event],
        session_id: str,
        ctx: Any | None = None,
    ) -> str | None:
        """Build a deterministic summary from event author/text pairs.

        Each event contributes a fragment of the form:
            author=normalized_text

        Fragments are joined with ' | ' and prefixed with the session id.
        """
        if not events:
            return None

        fragments: list[str] = []
        for event in events:
            text = (event.get_text() or "").strip()
            if not text:
                calls = event.get_function_calls()
                responses = event.get_function_responses()
                if calls:
                    text = "tool_call:" + ",".join(c.name or "" for c in calls)
                elif responses:
                    text = "tool_response:" + ",".join(r.name or "" for r in responses)
            if text:
                normalized_text = re.sub(r"\s+", " ", text).strip()
                fragments.append(f"{event.author or 'unknown'}={normalized_text}")

        if not fragments:
            return None

        return f"summary({session_id}): {' | '.join(fragments)} | facts={len(events)}-events"


def _make_session_config(*, store_historical_events: bool = False) -> SessionServiceConfig:
    """Create a session config with TTL disabled for deterministic replay."""
    config = SessionServiceConfig(store_historical_events=store_historical_events)
    config.clean_ttl_config()
    return config


def _make_memory_config() -> MemoryServiceConfig:
    """Create a memory config with TTL disabled for deterministic replay."""
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _make_summarizer_manager(keep_recent_count: int = 2) -> SummarizerSessionManager:
    """Build a summarizer manager with the deterministic summarizer."""
    model = _FakeModel(model_name="deterministic-replay-model")
    summarizer = DeterministicSessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda session: bool(session.events)],
        keep_recent_count=keep_recent_count,
    )
    return SummarizerSessionManager(model=model, summarizer=summarizer, auto_summarize=True)


def _sqlite_url(path: Path) -> str:
    """Build a SQLite connection URL from a path."""
    return f"sqlite:///{path.as_posix()}"


async def _close_services(session_service: Any, memory_service: Any) -> None:
    """Gracefully close both services."""
    await memory_service.close()
    await session_service.close()


async def build_backends(
    tmp_path: Path,
    session_config: SessionServiceConfig | None = None,
    *,
    keep_recent_count: int = 2,
) -> list[BackendBundle]:
    """Build the default set of replay backends.

    The default matrix is InMemory + SQLite (always available).  External
    SQL and Redis backends are only created when the corresponding
    environment variables are set.

    Args:
        tmp_path: Temporary directory for SQLite database files.
        session_config: Optional pre-built session configuration.
        keep_recent_count: Number of recent events to keep after summarization.

    Returns:
        A list of BackendBundle instances, ordered by availability.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    base_config = session_config or _make_session_config()
    memory_config = _make_memory_config()
    backends: list[BackendBundle] = []

    # ── InMemory ──────────────────────────────────────────────
    in_memory_session = InMemorySessionService(session_config=base_config.model_copy(deep=True))
    in_memory_session.set_summarizer_manager(_make_summarizer_manager(keep_recent_count), force=True)
    in_memory_memory = InMemoryMemoryService(memory_service_config=memory_config.model_copy(deep=True))
    backends.append(
        BackendBundle(
            name="inmemory",
            session_service=in_memory_session,
            memory_service=in_memory_memory,
            close=lambda s=in_memory_session, m=in_memory_memory: _close_services(s, m),
        )
    )

    # ── SQLite ────────────────────────────────────────────────
    sqlite_session = SqlSessionService(
        db_url=_sqlite_url(tmp_path / "replay_sessions.sqlite"),
        is_async=False,
        session_config=base_config.model_copy(deep=True),
    )
    sqlite_session.set_summarizer_manager(_make_summarizer_manager(keep_recent_count), force=True)
    sqlite_memory = SqlMemoryService(
        db_url=_sqlite_url(tmp_path / "replay_memory.sqlite"),
        is_async=False,
        memory_service_config=memory_config.model_copy(deep=True),
    )
    try:
        await sqlite_session._sql_storage.create_sql_engine()
        await sqlite_memory._sql_storage.create_sql_engine()
    except ValueError as exc:
        if isinstance(exc.__cause__, ImportError):
            pytest.skip(f"SQLite replay backend dependency is unavailable: {exc}")
        pytest.fail(f"SQLite replay backend failed to initialize: {exc}")

    backends.append(
        BackendBundle(
            name="sqlite",
            session_service=sqlite_session,
            memory_service=sqlite_memory,
            close=lambda s=sqlite_session, m=sqlite_memory: _close_services(s, m),
        )
    )

    # ── External SQL (env-var gated) ──────────────────────────
    external_sql_url = os.environ.get("TRPC_AGENT_REPLAY_SQL_URL")
    if external_sql_url:
        external_sql_session = SqlSessionService(
            db_url=external_sql_url,
            is_async=False,
            session_config=base_config.model_copy(deep=True),
        )
        external_sql_session.set_summarizer_manager(_make_summarizer_manager(keep_recent_count), force=True)
        external_sql_memory = SqlMemoryService(
            db_url=external_sql_url,
            is_async=False,
            memory_service_config=memory_config.model_copy(deep=True),
        )
        try:
            await external_sql_session._sql_storage.create_sql_engine()
            await external_sql_memory._sql_storage.create_sql_engine()
        except ValueError as exc:
            if isinstance(exc.__cause__, ImportError):
                pytest.skip(f"External SQL replay backend dependency is unavailable: {exc}")
            pytest.skip(f"External SQL replay backend failed to initialize: {exc}")

        backends.append(
            BackendBundle(
                name="external_sql",
                session_service=external_sql_session,
                memory_service=external_sql_memory,
                close=lambda s=external_sql_session, m=external_sql_memory: _close_services(s, m),
            )
        )

    # ── Redis (env-var gated) ─────────────────────────────────
    redis_url = os.environ.get("TRPC_AGENT_REPLAY_REDIS_URL")
    if redis_url:
        try:
            from trpc_agent_sdk.memory import RedisMemoryService
            from trpc_agent_sdk.sessions import RedisSessionService
        except ImportError as exc:
            pytest.skip(f"Redis replay backend dependency is unavailable: {exc}")

        redis_session = RedisSessionService(
            db_url=redis_url,
            is_async=False,
            session_config=base_config.model_copy(deep=True),
        )
        redis_session.set_summarizer_manager(_make_summarizer_manager(keep_recent_count), force=True)
        redis_memory = RedisMemoryService(
            db_url=redis_url,
            is_async=False,
            memory_service_config=memory_config.model_copy(deep=True),
        )
        backends.append(
            BackendBundle(
                name="redis",
                session_service=redis_session,
                memory_service=redis_memory,
                close=lambda s=redis_session, m=redis_memory: _close_services(s, m),
            )
        )

    return backends
