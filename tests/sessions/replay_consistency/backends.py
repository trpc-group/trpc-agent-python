# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Backend factories and execution functions for replay consistency tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import replace
import fnmatch
import os
import re
import time
from typing import AsyncGenerator
from typing import Any

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory._in_memory_memory_service import InMemoryMemoryService
from trpc_agent_sdk.memory._sql_memory_service import SqlMemoryService
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions._in_memory_session_service import InMemorySessionService
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._session_summarizer import SessionSummarizer
from trpc_agent_sdk.sessions._sql_session_service import SqlSessionService
from trpc_agent_sdk.sessions._summarizer_manager import SummarizerSessionManager
from trpc_agent_sdk.utils import user_key

from .fixtures import make_memory_config
from .fixtures import make_session_config
from .models import ReplayCase
from .normalizers import make_event
from .normalizers import normalize_memory_response
from .normalizers import normalize_session

BASELINE_BACKEND_NAME = "in_memory"
SQLITE_BACKEND_NAME = "sqlite_sql"
ENV_SQL_BACKEND_NAME = "env_sql"
ENV_REDIS_BACKEND_NAME = "env_redis"
MOCK_REDIS_BACKEND_NAME = "mock_redis"

SQL_URL_ENV = "TRPC_AGENT_REPLAY_SQL_URL"
REDIS_URL_ENV = "TRPC_AGENT_REPLAY_REDIS_URL"


class ReplayBackendUnavailable(RuntimeError):
    """Raised when an optional replay backend is requested but unavailable."""


@dataclass(frozen=True)
class ReplayBackendConfig:
    """Backend selection for replay consistency tests."""

    sql_url: str | None = None
    redis_url: str | None = None


DEFAULT_BACKEND_CONFIG = ReplayBackendConfig()


def resolve_backend_config(
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> ReplayBackendConfig:
    """Resolve optional external backend URLs from environment variables."""
    return replace(
        backend_config,
        sql_url=backend_config.sql_url or os.getenv(SQL_URL_ENV) or None,
        redis_url=backend_config.redis_url or os.getenv(REDIS_URL_ENV) or None,
    )


def configured_session_backend_names(
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> list[str]:
    """Return configured session backend names."""
    backend_config = resolve_backend_config(backend_config)
    names = [BASELINE_BACKEND_NAME]
    if backend_config.sql_url:
        names.append(ENV_SQL_BACKEND_NAME)
    if backend_config.redis_url:
        names.append(ENV_REDIS_BACKEND_NAME)
    return names


def configured_memory_backend_names(
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> list[str]:
    """Return configured memory backend names."""
    backend_config = resolve_backend_config(backend_config)
    names = [BASELINE_BACKEND_NAME]
    if backend_config.sql_url:
        names.append(ENV_SQL_BACKEND_NAME)
    if backend_config.redis_url:
        names.append(ENV_REDIS_BACKEND_NAME)
    return names


def default_backend_matrix_enabled(
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> bool:
    """Return whether the deterministic default backend matrix is active."""
    return (
        backend_config == DEFAULT_BACKEND_CONFIG
        and not os.getenv(SQL_URL_ENV)
        and not os.getenv(REDIS_URL_ENV)
    )


def comparison_backend_names(snapshots: dict[str, dict[str, Any]]) -> list[str]:
    """Return backend names to compare against the InMemory baseline."""
    return [name for name in snapshots if name != BASELINE_BACKEND_NAME]


class ReplayMockRedisStorage:
    """In-memory RedisStorage replacement for replay tests."""

    def __init__(self) -> None:
        self._strings: dict[str, Any] = {}
        self._hashes: dict[str, dict[str, Any]] = {}
        self._lists: dict[str, list[Any]] = {}

    @asynccontextmanager
    async def create_db_session(self):
        yield object()

    async def execute_command(self, session: Any, command: Any) -> Any:
        """Execute the Redis command subset used by replay services."""
        method = command.method.lower()
        args = command.args

        if method == "set":
            self._strings[args[0]] = args[1]
            return True
        if method == "get":
            return self._strings.get(args[0])
        if method == "keys":
            return sorted(self._matching_keys(args[0]))
        if method == "hset":
            key = args[0]
            self._hashes.setdefault(key, {})
            for index in range(1, len(args), 2):
                self._hashes[key][args[index]] = args[index + 1]
            return True
        if method == "hgetall":
            return dict(self._hashes.get(args[0], {}))
        if method == "rpush":
            key = args[0]
            self._lists.setdefault(key, []).extend(args[1:])
            return len(self._lists[key])
        if method == "lrange":
            values = self._lists.get(args[0], [])
            start = args[1]
            stop = args[2]
            end = None if stop == -1 else stop + 1
            return values[start:end]
        if method == "type":
            key = args[0]
            if key in self._strings:
                return "string"
            if key in self._hashes:
                return "hash"
            if key in self._lists:
                return "list"
            return "none"
        return None

    async def query(self, session: Any, pattern: str, conditions: Any) -> list[tuple[str, Any]]:
        """Query matching mock Redis keys."""
        keys = sorted(self._matching_keys(pattern))
        if conditions.limit > 0:
            keys = keys[:conditions.limit]

        results = []
        for key in keys:
            if key in self._strings:
                results.append((key, self._strings[key]))
            elif key in self._hashes:
                results.append((key, dict(self._hashes[key])))
            elif key in self._lists:
                results.append((key, list(self._lists[key])))
        return results

    async def delete(self, session: Any, key: str, conditions: Any = None) -> None:
        """Delete a mock Redis key."""
        self._strings.pop(key, None)
        self._hashes.pop(key, None)
        self._lists.pop(key, None)

    async def expire(self, session: Any, expire_obj: Any) -> None:
        """Ignore TTL in the replay mock."""

    async def close(self) -> None:
        """Close mock storage."""

    def _matching_keys(self, pattern: str) -> list[str]:
        keys = set(self._strings) | set(self._hashes) | set(self._lists)
        return [key for key in keys if fnmatch.fnmatch(key, pattern)]


class FakeReplayModel(LLMModel):
    """Concrete test model used only to satisfy SessionSummarizer's model contract."""

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
    """No-LLM summarizer that still uses the real session summary flow."""

    async def _compress_session_to_summary(
        self,
        events: list[Event],
        session_id: str,
        ctx: Any | None = None,
    ) -> str | None:
        fragments = []
        ordered_events = sorted(
            events,
            key=lambda event: (
                not event.is_summary_event(),
                event.timestamp or 0,
                event.invocation_id or "",
                event.author or "",
                event.get_text() or "",
            ),
        )
        for event in ordered_events:
            text = (event.get_text() or "").strip()
            if not text:
                continue
            normalized_text = re.sub(r"\s+", " ", text).strip()
            fragments.append(f"{event.author or 'unknown'}={normalized_text}")
        if not fragments:
            return None
        return f"summary({session_id}): {' | '.join(fragments)} | facts={len(events)}-events"


def make_summarizer_manager(keep_recent_count: int = 2) -> SummarizerSessionManager:
    """Create a deterministic summarizer manager for replay summary cases."""
    model = FakeReplayModel(model_name="deterministic-replay-model")
    summarizer = DeterministicSessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda session: bool(session.events)],
        keep_recent_count=keep_recent_count,
    )
    return SummarizerSessionManager(model=model, summarizer=summarizer, auto_summarize=True)


async def create_sql_session_service(
    session_config: Any,
    db_url: str = "sqlite:///:memory:",
) -> SqlSessionService:
    """Create a SQL session service."""
    service = SqlSessionService(
        db_url=db_url,
        session_config=session_config,
        is_async=False,
    )
    await service._sql_storage.create_sql_engine()
    return service


async def create_sql_memory_service(memory_config: Any) -> SqlMemoryService:
    """Create a SQLite in-memory SQL memory service."""
    return await create_sql_memory_service_for_url(memory_config, "sqlite:///:memory:")


async def create_sql_memory_service_for_url(memory_config: Any, db_url: str) -> SqlMemoryService:
    """Create a SQL memory service."""
    service = SqlMemoryService(db_url=db_url, memory_service_config=memory_config, is_async=False)
    await service._sql_storage.create_sql_engine()
    return service


def create_redis_session_service(session_config: Any, db_url: str) -> Any:
    """Create a Redis session service."""
    try:
        from trpc_agent_sdk.sessions._redis_session_service import RedisSessionService
    except ImportError as ex:
        raise ReplayBackendUnavailable("Redis session dependencies are not installed") from ex
    return RedisSessionService(db_url=db_url, session_config=session_config, is_async=False)


def create_mock_redis_session_service(session_config: Any) -> Any:
    """Create a Redis session service backed by in-memory mock storage."""
    service = create_redis_session_service(session_config, "redis://mock")
    service._redis_storage = ReplayMockRedisStorage()
    return service


def create_redis_memory_service(memory_config: Any, db_url: str) -> Any:
    """Create a Redis memory service."""
    try:
        from trpc_agent_sdk.memory._redis_memory_service import RedisMemoryService
    except ImportError as ex:
        raise ReplayBackendUnavailable("Redis memory dependencies are not installed") from ex
    return RedisMemoryService(db_url=db_url, memory_service_config=memory_config, is_async=False)


def create_mock_redis_memory_service(memory_config: Any) -> Any:
    """Create a Redis memory service backed by in-memory mock storage."""
    service = create_redis_memory_service(memory_config, "redis://mock")
    service._redis_storage = ReplayMockRedisStorage()
    return service


async def create_session_services(
    replay_case: ReplayCase,
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, Any]:
    """Create configured session services for a replay case."""
    backend_config = resolve_backend_config(backend_config)
    session_config = make_session_config(replay_case.session_config)
    services = {
        BASELINE_BACKEND_NAME: InMemorySessionService(session_config=session_config),
    }
    if backend_config.sql_url:
        try:
            services[ENV_SQL_BACKEND_NAME] = await create_sql_session_service(
                make_session_config(replay_case.session_config),
                db_url=backend_config.sql_url,
            )
        except Exception:
            services[SQLITE_BACKEND_NAME] = await create_sql_session_service(
                make_session_config(replay_case.session_config),
            )
    if backend_config.redis_url:
        try:
            services[ENV_REDIS_BACKEND_NAME] = create_redis_session_service(
                make_session_config(replay_case.session_config),
                backend_config.redis_url,
            )
        except Exception:
            services[MOCK_REDIS_BACKEND_NAME] = create_mock_redis_session_service(
                make_session_config(replay_case.session_config),
            )
    return services


async def create_memory_services(
    replay_case: ReplayCase,
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, Any]:
    """Create configured memory services for a replay case."""
    backend_config = resolve_backend_config(backend_config)
    memory_config = make_memory_config(replay_case.memory_config)
    services = {
        BASELINE_BACKEND_NAME: InMemoryMemoryService(memory_service_config=memory_config),
    }
    if backend_config.sql_url:
        try:
            services[ENV_SQL_BACKEND_NAME] = await create_sql_memory_service_for_url(
                make_memory_config(replay_case.memory_config),
                backend_config.sql_url,
            )
        except Exception:
            services[SQLITE_BACKEND_NAME] = await create_sql_memory_service(
                make_memory_config(replay_case.memory_config),
            )
    if backend_config.redis_url:
        try:
            services[ENV_REDIS_BACKEND_NAME] = create_redis_memory_service(
                make_memory_config(replay_case.memory_config),
                backend_config.redis_url,
            )
        except Exception:
            services[MOCK_REDIS_BACKEND_NAME] = create_mock_redis_memory_service(
                make_memory_config(replay_case.memory_config),
            )
    return services


async def get_required_session(service: Any, replay_case: ReplayCase) -> Session:
    """Get a session from the service, asserting it exists."""
    stored = await service.get_session(
        app_name=replay_case.app_name,
        user_id=replay_case.user_id,
        session_id=replay_case.session_id,
    )
    assert stored is not None
    return stored


def make_memory_session(replay_case: ReplayCase) -> Session:
    """Create a session for memory testing."""
    base_timestamp = 1700000000.0
    return Session(
        id=replay_case.session_id,
        app_name=replay_case.app_name,
        user_id=replay_case.user_id,
        save_key=user_key(replay_case.app_name, replay_case.user_id),
        events=[make_event(event_record, base_timestamp) for event_record in replay_case.event_records],
    )


async def run_session_service_replay(service: Any, replay_case: ReplayCase) -> dict[str, Any]:
    """Run one session replay case against a single service."""
    base_timestamp = time.time()
    session = await service.create_session(
        app_name=replay_case.app_name,
        user_id=replay_case.user_id,
        session_id=replay_case.session_id,
    )
    summary_texts = []
    for event_index, event_record in enumerate(replay_case.event_records):
        await service.append_event(session, make_event(event_record, base_timestamp))
        if event_index in replay_case.summary_points:
            await service.create_session_summary(session)
            summary_text = await service.get_session_summary(session)
            assert summary_text
            summary_texts.append(summary_text)
            session = await get_required_session(service, replay_case)

    if len(summary_texts) > 1:
        assert summary_texts[0] != summary_texts[-1]

    stored = await get_required_session(service, replay_case)
    return await normalize_session(stored, service)


async def create_session_fallback_service(backend_name: str, replay_case: ReplayCase) -> tuple[str, Any] | None:
    """Create a local fallback service for an unavailable external session backend."""
    if backend_name == ENV_SQL_BACKEND_NAME:
        return (
            SQLITE_BACKEND_NAME,
            await create_sql_session_service(make_session_config(replay_case.session_config)),
        )
    if backend_name == ENV_REDIS_BACKEND_NAME:
        return (
            MOCK_REDIS_BACKEND_NAME,
            create_mock_redis_session_service(make_session_config(replay_case.session_config)),
        )
    return None


async def run_session_replay_case(
    replay_case: ReplayCase,
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, dict[str, Any]]:
    """Run the same deterministic session trajectory against configured session backends."""
    services = await create_session_services(replay_case, backend_config)
    if replay_case.summary_points:
        for service in services.values():
            service.set_summarizer_manager(make_summarizer_manager(), force=True)
    fallback_services = []
    try:
        snapshots = {}
        for backend_name, service in list(services.items()):
            try:
                snapshots[backend_name] = await run_session_service_replay(service, replay_case)
            except Exception as ex:
                fallback = await create_session_fallback_service(backend_name, replay_case)
                if fallback is None:
                    raise
                fallback_name, fallback_service = fallback
                if replay_case.summary_points:
                    fallback_service.set_summarizer_manager(make_summarizer_manager(), force=True)
                fallback_services.append(fallback_service)
                try:
                    snapshots[fallback_name] = await run_session_service_replay(fallback_service, replay_case)
                except Exception as fallback_ex:
                    raise ReplayBackendUnavailable(
                        f"Optional session backend {backend_name!r} is unavailable and "
                        f"fallback {fallback_name!r} failed: {fallback_ex}"
                    ) from ex
        return snapshots
    finally:
        for service in list(services.values()) + fallback_services:
            await service.close()


async def run_memory_service_replay(service: Any, replay_case: ReplayCase) -> dict[str, Any]:
    """Run one memory replay case against a single service."""
    session = make_memory_session(replay_case)
    await service.store_session(session)
    searches = []
    for search_record in replay_case.memory_search_records:
        response = await service.search_memory(
            session.save_key,
            search_record["query"],
            limit=search_record.get("limit", 10),
        )
        searches.append({
            "query": search_record["query"],
            "limit": search_record.get("limit", 10),
            "memories": normalize_memory_response(response),
        })
    return {"searches": searches}


async def create_memory_fallback_service(backend_name: str, replay_case: ReplayCase) -> tuple[str, Any] | None:
    """Create a local fallback service for an unavailable external memory backend."""
    if backend_name == ENV_SQL_BACKEND_NAME:
        return (
            SQLITE_BACKEND_NAME,
            await create_sql_memory_service(make_memory_config(replay_case.memory_config)),
        )
    if backend_name == ENV_REDIS_BACKEND_NAME:
        return (
            MOCK_REDIS_BACKEND_NAME,
            create_mock_redis_memory_service(make_memory_config(replay_case.memory_config)),
        )
    return None


async def run_memory_replay_case(
    replay_case: ReplayCase,
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, dict[str, Any]]:
    """Store the same deterministic session into configured memory backends and search it."""
    services = await create_memory_services(replay_case, backend_config)
    fallback_services = []
    try:
        snapshots = {}
        for backend_name, service in list(services.items()):
            try:
                snapshots[backend_name] = await run_memory_service_replay(service, replay_case)
            except Exception as ex:
                fallback = await create_memory_fallback_service(backend_name, replay_case)
                if fallback is None:
                    raise
                fallback_name, fallback_service = fallback
                fallback_services.append(fallback_service)
                try:
                    snapshots[fallback_name] = await run_memory_service_replay(fallback_service, replay_case)
                except Exception as fallback_ex:
                    raise ReplayBackendUnavailable(
                        f"Optional memory backend {backend_name!r} is unavailable and "
                        f"fallback {fallback_name!r} failed: {fallback_ex}"
                    ) from ex
        return snapshots
    finally:
        for service in list(services.values()) + fallback_services:
            await service.close()
