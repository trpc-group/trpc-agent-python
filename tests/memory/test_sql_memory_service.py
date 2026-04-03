# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.memory._sql_memory_service.

Covers:
- MemStorageEvent: from_event, to_event, update_event, long_running_tool_ids property
- SqlMemoryService: store_session, search_memory, close, cleanup
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory._sql_memory_service import MemStorageData, MemStorageEvent, SqlMemoryService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content, GroundingMetadata, Part, SearchMemoryResponse, Ttl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    text: str = "hello world",
    author: str = "user",
    event_id: str = "",
    timestamp: Optional[float] = None,
) -> Event:
    return Event(
        id=event_id or Event.new_id(),
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=timestamp or time.time(),
    )


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


def _make_config_with_ttl(ttl_seconds: int = 3600, cleanup_interval: float = 0.0) -> MemoryServiceConfig:
    ttl = MemoryServiceConfig.create_ttl_config(
        enable=True, ttl_seconds=ttl_seconds, cleanup_interval_seconds=cleanup_interval
    )
    return MemoryServiceConfig(enabled=True, ttl=ttl)


class _FakeSqlSession:
    pass


@asynccontextmanager
async def _fake_create_db_session():
    yield _FakeSqlSession()


def _patch_sql_storage():
    mock_storage = MagicMock()
    mock_storage.create_db_session = _fake_create_db_session
    mock_storage.get = AsyncMock(return_value=None)
    mock_storage.add = AsyncMock()
    mock_storage.commit = AsyncMock()
    mock_storage.query = AsyncMock(return_value=[])
    mock_storage.delete = AsyncMock()
    mock_storage.close = AsyncMock()
    return mock_storage


# ---------------------------------------------------------------------------
# MemStorageEvent — from_event / to_event / update_event
# ---------------------------------------------------------------------------


class TestMemStorageEvent:
    def test_from_event_basic(self):
        event = _make_event("hello world", author="user", event_id="e1")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)

        assert storage_event.id == "e1"
        assert storage_event.author == "user"
        assert storage_event.save_key == "app/user1"
        assert storage_event.session_id == "session-1"
        assert storage_event.content is not None

    def test_from_event_with_grounding_metadata(self):
        event = _make_event("hello", event_id="e1")
        event.grounding_metadata = GroundingMetadata()
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        assert storage_event.grounding_metadata is not None

    def test_from_event_with_custom_metadata(self):
        event = _make_event("hello", event_id="e1")
        event.custom_metadata = {"key": "value"}
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        assert storage_event.custom_metadata == {"key": "value"}

    def test_from_event_without_content(self):
        event = Event(id="e1", invocation_id="inv-1", author="user")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        assert storage_event.content is None

    def test_to_event_roundtrip(self):
        original = _make_event("hello world", author="assistant", event_id="e1")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, original)
        restored = storage_event.to_event()

        assert restored.id == "e1"
        assert restored.author == "assistant"
        assert restored.content is not None
        assert restored.content.parts[0].text == "hello world"

    def test_update_event(self):
        event1 = _make_event("first", event_id="e1")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event1)

        event2 = _make_event("second", author="assistant", event_id="e1")
        storage_event.update_event(session, event2)

        assert storage_event.author == "assistant"

    def test_update_event_with_metadata(self):
        event1 = _make_event("first", event_id="e1")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event1)

        event2 = _make_event("second", event_id="e1")
        event2.grounding_metadata = GroundingMetadata()
        event2.custom_metadata = {"updated": True}
        storage_event.update_event(session, event2)
        assert storage_event.custom_metadata == {"updated": True}
        assert storage_event.grounding_metadata is not None

    def test_long_running_tool_ids_roundtrip(self):
        event = _make_event("hello", event_id="e1")
        event.long_running_tool_ids = {"tool1", "tool2"}
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        restored = storage_event.to_event()
        assert restored.long_running_tool_ids == {"tool1", "tool2"}

    def test_long_running_tool_ids_none_roundtrip(self):
        event = _make_event("hello", event_id="e1")
        event.long_running_tool_ids = None
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        assert storage_event.long_running_tool_ids == set()

    def test_long_running_tool_ids_set_via_from_event(self):
        event = _make_event("hello", event_id="e1")
        event.long_running_tool_ids = {"a", "b"}
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        parsed = set(json.loads(storage_event.long_running_tool_ids_json))
        assert parsed == {"a", "b"}

    def test_long_running_tool_ids_empty_set(self):
        event = _make_event("hello", event_id="e1")
        event.long_running_tool_ids = set()
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        restored = storage_event.to_event()
        assert restored.long_running_tool_ids == set()

    def test_from_event_preserves_long_running_tool_ids(self):
        event = _make_event("hello", event_id="e1")
        event.long_running_tool_ids = {"t1", "t2"}
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        assert storage_event.long_running_tool_ids == {"t1", "t2"}

    def test_from_event_preserves_flags(self):
        event = _make_event("hello", event_id="e1")
        event.partial = True
        event.turn_complete = True
        event.interrupted = True
        event.error_code = "ERR"
        event.error_message = "something went wrong"
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        assert storage_event.partial is True
        assert storage_event.turn_complete is True
        assert storage_event.interrupted is True
        assert storage_event.error_code == "ERR"
        assert storage_event.error_message == "something went wrong"


# ---------------------------------------------------------------------------
# MemStorageData
# ---------------------------------------------------------------------------


class TestMemStorageData:
    def test_base_class_exists(self):
        assert MemStorageData is not None
        assert hasattr(MemStorageData, "metadata")


# ---------------------------------------------------------------------------
# SqlMemoryService — store_session
# ---------------------------------------------------------------------------


class TestSqlStoreSession:
    async def test_store_basic(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        session = _make_session(events=[_make_event("hello")])
        await svc.store_session(session)
        svc._sql_storage.add.assert_called_once()
        svc._sql_storage.commit.assert_called_once()

    async def test_store_updates_existing_event(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        existing = MagicMock(spec=MemStorageEvent)
        svc._sql_storage.get = AsyncMock(return_value=existing)

        session = _make_session(events=[_make_event("hello")])
        await svc.store_session(session)
        existing.update_event.assert_called_once()
        svc._sql_storage.add.assert_not_called()

    async def test_store_skips_events_without_content(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        event_no_content = Event(id=Event.new_id(), invocation_id="inv-1", author="user")
        session = _make_session(events=[event_no_content])
        await svc.store_session(session)
        svc._sql_storage.add.assert_not_called()
        svc._sql_storage.commit.assert_not_called()

    async def test_store_raises_on_non_session(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        with pytest.raises(TypeError, match="Content must be a Session"):
            await svc.store_session("not a session")


# ---------------------------------------------------------------------------
# SqlMemoryService — search_memory
# ---------------------------------------------------------------------------


class TestSqlSearchMemory:
    async def test_search_empty(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        result = await svc.search_memory("app/user1", "hello")
        assert isinstance(result, SearchMemoryResponse)
        assert result.memories == []

    async def test_search_with_match(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        event = _make_event("hello world", author="assistant")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        svc._sql_storage.query = AsyncMock(return_value=[storage_event])

        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1
        assert result.memories[0].author == "assistant"

    async def test_search_no_match(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        event = _make_event("hello world")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        svc._sql_storage.query = AsyncMock(return_value=[storage_event])

        result = await svc.search_memory("app/user1", "zzzzz")
        assert len(result.memories) == 0

    async def test_search_commits_on_match(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        event = _make_event("hello world")
        session = _make_session()
        storage_event = MemStorageEvent.from_event(session, event)
        svc._sql_storage.query = AsyncMock(return_value=[storage_event])

        await svc.search_memory("app/user1", "hello")
        svc._sql_storage.commit.assert_called_once()

    async def test_search_skips_event_without_text(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        event_no_text = Event(
            id="e-no-text", invocation_id="inv-1", author="user",
            content=Content(parts=[Part()]),
        )
        session = _make_session()
        storage_no_text = MemStorageEvent.from_event(session, event_no_text)

        event_valid = _make_event("hello world")
        storage_valid = MemStorageEvent.from_event(session, event_valid)

        svc._sql_storage.query = AsyncMock(return_value=[storage_no_text, storage_valid])

        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1


# ---------------------------------------------------------------------------
# SqlMemoryService — close
# ---------------------------------------------------------------------------


class TestSqlClose:
    async def test_close_delegates(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        await svc.close()
        svc._sql_storage.close.assert_called_once()


# ---------------------------------------------------------------------------
# SqlMemoryService — cleanup task
# ---------------------------------------------------------------------------


class TestSqlCleanupTask:
    async def test_stop_cleanup_when_no_task(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None
        svc._stop_cleanup_task()  # should not raise

    async def test_start_cleanup_no_ttl(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None
        svc._start_cleanup_task()
        assert svc._SqlMemoryService__cleanup_task is None

    async def test_start_and_stop_cleanup_with_ttl(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0)
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        svc._start_cleanup_task()
        assert svc._SqlMemoryService__cleanup_task is not None
        svc._stop_cleanup_task()
        assert svc._SqlMemoryService__cleanup_task is None

    async def test_start_cleanup_idempotent(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0)
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        svc._start_cleanup_task()
        task = svc._SqlMemoryService__cleanup_task
        svc._start_cleanup_task()
        assert svc._SqlMemoryService__cleanup_task is task
        svc._stop_cleanup_task()

    async def test_cleanup_expired_async(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_with_ttl(ttl_seconds=10, cleanup_interval=0.0)
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        fake_expired = [MagicMock()]
        svc._sql_storage.query = AsyncMock(return_value=fake_expired)

        await svc._cleanup_expired_async()
        svc._sql_storage.delete.assert_called_once()
        svc._sql_storage.commit.assert_called_once()

    async def test_cleanup_expired_no_expired(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_with_ttl(ttl_seconds=10, cleanup_interval=0.0)
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        svc._sql_storage.query = AsyncMock(return_value=[])

        await svc._cleanup_expired_async()
        svc._sql_storage.delete.assert_not_called()


# ---------------------------------------------------------------------------
# SqlMemoryService — _cleanup_loop
# ---------------------------------------------------------------------------


class TestSqlCleanupLoop:
    async def test_cleanup_loop_runs_and_stops(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=0.05)
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        svc._start_cleanup_task()
        await asyncio.sleep(0.1)
        svc._stop_cleanup_task()

    async def test_cleanup_loop_handles_error(self):
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=0.05)
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        with patch.object(SqlMemoryService, "_cleanup_expired_async", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            svc._start_cleanup_task()
            await asyncio.sleep(0.15)
            svc._stop_cleanup_task()

    async def test_search_event_no_content_parts_skipped(self):
        """Cover the branch where to_event() yields event without content."""
        svc = SqlMemoryService.__new__(SqlMemoryService)
        svc._memory_service_config = _make_config_no_ttl()
        svc._sql_storage = _patch_sql_storage()
        svc._SqlMemoryService__cleanup_task = None
        svc._SqlMemoryService__cleanup_stop_event = None

        event_no_content = Event(id="e-no-c", invocation_id="inv-1", author="user")
        session = _make_session()
        storage_no_content = MemStorageEvent.from_event(session, event_no_content)

        svc._sql_storage.query = AsyncMock(return_value=[storage_no_content])

        result = await svc.search_memory("app/user1", "hello")
        assert result.memories == []
