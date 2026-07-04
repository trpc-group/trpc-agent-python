"""Backend construction for replay consistency tests."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import re
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
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SummarizerSessionManager


@dataclass
class BackendBundle:
    name: str
    session_service: Any
    memory_service: Any
    close: Callable[[], Awaitable[None]]


class FakeModel(LLMModel):
    """Concrete model object for summarizer metadata; it is never called."""

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"deterministic-replay-model"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: Any | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        if False:
            yield LlmResponse()


class DeterministicSessionSummarizer(SessionSummarizer):
    """A no-LLM summarizer that still uses SessionSummarizer compression."""

    async def _compress_session_to_summary(
        self,
        events: list[Event],
        session_id: str,
        ctx: Any | None = None,
    ) -> str | None:
        if not events:
            return None

        fragments: list[str] = []
        for event in events:
            text = (event.get_text() or "").strip()
            if not text:
                calls = event.get_function_calls()
                responses = event.get_function_responses()
                if calls:
                    text = "tool_call:" + ",".join(call.name or "" for call in calls)
                elif responses:
                    text = "tool_response:" + ",".join(response.name or "" for response in responses)
            if text:
                normalized_text = re.sub(r"\s+", " ", text).strip()
                fragments.append(f"{event.author or 'unknown'}={normalized_text}")

        if not fragments:
            return None

        return f"summary({session_id}): {' | '.join(fragments)} | facts={len(events)}-events"


def make_session_config(*, store_historical_events: bool = False) -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=store_historical_events)
    config.clean_ttl_config()
    return config


def _make_memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _make_summarizer_manager(keep_recent_count: int = 2) -> SummarizerSessionManager:
    model = FakeModel(model_name="deterministic-replay-model")
    summarizer = DeterministicSessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda session: bool(session.events)],
        keep_recent_count=keep_recent_count,
    )
    return SummarizerSessionManager(model=model, summarizer=summarizer, auto_summarize=True)


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


async def _close_services(session_service: Any, memory_service: Any) -> None:
    await memory_service.close()
    await session_service.close()


async def build_backends(
    tmp_path: Path,
    session_config: SessionServiceConfig | None = None,
    *,
    keep_recent_count: int = 2,
) -> list[BackendBundle]:
    """Build default replay backends.

    SQLite is part of the default matrix. Its initialization must either
    succeed, skip for a missing optional dependency, or fail the test.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    base_config = session_config or make_session_config()
    memory_config = _make_memory_config()
    backends: list[BackendBundle] = []

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
