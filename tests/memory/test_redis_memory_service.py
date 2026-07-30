# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.memory._redis_memory_service.

Covers:
- RedisMemoryService.__init__: config, storage creation
- RedisMemoryService.store_session: event serialization, rpush, TTL
- RedisMemoryService.search_memory: keyword matching, limit, TTL refresh
- RedisMemoryService.close: delegates to storage
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory._redis_memory_service import RedisMemoryService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content, Part, SearchMemoryResponse, Ttl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(text: str = "hello world", author: str = "user") -> Event:
    return Event(
        id=Event.new_id(),
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=time.time(),
    )


def _make_event_no_content() -> Event:
    return Event(id=Event.new_id(), invocation_id="inv-1", author="user")


def _make_session(
    events: Optional[list[Event]] = None,
    save_key: str = "app/user1",
    session_id: str = "session-1",
) -> Session:
    return Session(
        id=session_id,
        app_name="app",
        user_id="user1",
        save_key=save_key,
        events=events or [],
    )


def _make_config_no_ttl() -> MemoryServiceConfig:
    cfg = MemoryServiceConfig(enabled=True)
    cfg.clean_ttl_config()
    return cfg


class _FakeRedisSession:
    """Minimal Redis session stub."""
    pass


@asynccontextmanager
async def _fake_create_db_session():
    yield _FakeRedisSession()


def _patch_redis_storage():
    """Patch RedisStorage so it doesn't connect to a real Redis."""
    mock_storage = MagicMock()
    mock_storage.create_db_session = _fake_create_db_session
    mock_storage.delete = AsyncMock()
    mock_storage.execute_command = AsyncMock()
    mock_storage.query = AsyncMock(return_value=[])
    mock_storage.expire = AsyncMock()
    mock_storage.close = AsyncMock()
    return mock_storage


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestRedisInit:
    @patch("trpc_agent_sdk.memory._redis_memory_service.RedisStorage")
    def test_default_init(self, MockRedisStorage):
        MockRedisStorage.return_value = MagicMock()
        svc = RedisMemoryService(db_url="redis://localhost", memory_service_config=_make_config_no_ttl())
        assert svc.enabled is True
        MockRedisStorage.assert_called_once()

    @patch("trpc_agent_sdk.memory._redis_memory_service.RedisStorage")
    def test_passes_is_async(self, MockRedisStorage):
        MockRedisStorage.return_value = MagicMock()
        RedisMemoryService(db_url="redis://localhost", is_async=True, memory_service_config=_make_config_no_ttl())
        call_kwargs = MockRedisStorage.call_args
        assert call_kwargs.kwargs.get("is_async") is True or call_kwargs[1].get("is_async") is True


# ---------------------------------------------------------------------------
# store_session
# ---------------------------------------------------------------------------


class TestRedisStoreSession:
    async def test_store_basic(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        session = _make_session(events=[_make_event("hello")])
        await svc.store_session(session)

        svc._redis_storage.delete.assert_called_once()
        svc._redis_storage.execute_command.assert_called_once()

    async def test_store_skips_events_without_content(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        session = _make_session(events=[_make_event_no_content()])
        await svc.store_session(session)

        svc._redis_storage.delete.assert_not_called()
        svc._redis_storage.execute_command.assert_not_called()

    async def test_store_multiple_events(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        session = _make_session(events=[_make_event("hello"), _make_event("world")])
        await svc.store_session(session)

        svc._redis_storage.execute_command.assert_called_once()
        cmd = svc._redis_storage.execute_command.call_args[0][1]
        assert cmd.method == "rpush"


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


class TestRedisSearchMemory:
    async def test_search_empty(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()
        svc._redis_storage.query = AsyncMock(return_value=[])

        result = await svc.search_memory("app/user1", "hello")
        assert isinstance(result, SearchMemoryResponse)
        assert result.memories == []

    async def test_search_with_matches(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = _make_event("hello world")
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("memory:app/user1:s1", [event_json])])

        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1

    async def test_search_no_keyword_match(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = _make_event("hello world")
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("memory:app/user1:s1", [event_json])])

        result = await svc.search_memory("app/user1", "zzzzz")
        assert len(result.memories) == 0

    async def test_search_respects_limit(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        events_json = [_make_event(f"hello item {i}").model_dump_json() for i in range(5)]
        svc._redis_storage.query = AsyncMock(return_value=[("memory:app/user1:s1", events_json)])

        result = await svc.search_memory("app/user1", "hello", limit=2)
        assert len(result.memories) == 2

    async def test_search_refreshes_ttl_on_match(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = _make_event("hello world")
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("memory:app/user1:s1", [event_json])])

        await svc.search_memory("app/user1", "hello")
        svc._redis_storage.expire.assert_called_once()

    async def test_search_handles_non_list_event_json(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = _make_event("hello world")
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("memory:app/user1:s1", event_json)])

        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1

    async def test_search_handles_invalid_json(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        svc._redis_storage.query = AsyncMock(return_value=[("memory:app/user1:s1", ["invalid-json"])])

        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 0

    async def test_search_memory_entry_fields(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = _make_event("hello world", author="assistant")
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("key", [event_json])])

        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1
        entry = result.memories[0]
        assert entry.author == "assistant"
        assert entry.timestamp is not None
        assert entry.content is not None


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestRedisSearchEdgeCases:
    async def test_search_event_without_content_parts(self):
        """Cover L86-87: event parsed but has no content/parts."""
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = Event(id=Event.new_id(), invocation_id="inv-1", author="user")
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("key", [event_json])])

        result = await svc.search_memory("app/user1", "hello")
        assert result.memories == []

    async def test_search_event_with_empty_text_parts(self):
        """Cover L90-91: event has parts but no text content."""
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = Event(
            id=Event.new_id(), invocation_id="inv-1", author="user",
            content=Content(parts=[Part()]),
        )
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("key", [event_json])])

        result = await svc.search_memory("app/user1", "hello")
        assert result.memories == []

    async def test_search_does_not_refresh_ttl_when_no_match(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        event = _make_event("hello world")
        event_json = event.model_dump_json()
        svc._redis_storage.query = AsyncMock(return_value=[("key", [event_json])])

        await svc.search_memory("app/user1", "zzzzz")
        svc._redis_storage.expire.assert_not_called()


class TestRedisClose:
    async def test_close_delegates(self):
        svc = RedisMemoryService.__new__(RedisMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._redis_storage = _patch_redis_storage()

        await svc.close()
        svc._redis_storage.close.assert_called_once()
