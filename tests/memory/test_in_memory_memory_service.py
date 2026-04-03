# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.memory._in_memory_memory_service.

Covers:
- EventTtl: is_expired, update_expired_at
- InMemoryMemoryService: store_session, search_memory, cleanup, close
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional
from unittest.mock import patch

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory._in_memory_memory_service import EventTtl, InMemoryMemoryService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content, Part, SearchMemoryResponse, Ttl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(text: str = "hello world", author: str = "user", event_id: str = "") -> Event:
    return Event(
        id=event_id or Event.new_id(),
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=time.time(),
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


# ---------------------------------------------------------------------------
# EventTtl
# ---------------------------------------------------------------------------


class TestEventTtl:
    def test_default_ttl(self):
        event = _make_event()
        et = EventTtl(event=event)
        assert et.event is event
        assert et.ttl is not None

    def test_is_expired_with_custom_ttl(self):
        ttl = Ttl(enable=True, ttl_seconds=10, cleanup_interval_seconds=60.0)
        event = _make_event()
        et = EventTtl(event=event, ttl=ttl)
        assert not et.is_expired()

    def test_is_expired_returns_true_when_past_ttl(self):
        ttl = Ttl(enable=True, ttl_seconds=1, cleanup_interval_seconds=60.0)
        event = _make_event()
        et = EventTtl(event=event, ttl=ttl)
        future = time.time() + 100
        assert et.is_expired(now=future)

    def test_is_expired_disabled_ttl(self):
        ttl = Ttl(enable=False)
        et = EventTtl(event=_make_event(), ttl=ttl)
        assert not et.is_expired(now=time.time() + 999999)

    def test_update_expired_at_refreshes(self):
        ttl = Ttl(enable=True, ttl_seconds=10, cleanup_interval_seconds=60.0)
        event = _make_event()
        et = EventTtl(event=event, ttl=ttl)
        old_update_time = et.ttl.update_time
        time.sleep(0.01)
        et.update_expired_at()
        assert et.ttl.update_time >= old_update_time


# ---------------------------------------------------------------------------
# InMemoryMemoryService — store_session
# ---------------------------------------------------------------------------


class TestInMemoryStoreSession:
    async def test_store_basic_session(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        session = _make_session(events=[_make_event("hello")])
        await svc.store_session(session)
        assert "app/user1" in svc._session_events
        assert "session-1" in svc._session_events["app/user1"]
        assert len(svc._session_events["app/user1"]["session-1"]) == 1
        await svc.close()

    async def test_store_multiple_events(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        events = [_make_event("hello"), _make_event("world")]
        session = _make_session(events=events)
        await svc.store_session(session)
        assert len(svc._session_events["app/user1"]["session-1"]) == 2
        await svc.close()

    async def test_store_overwrites_session_events(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        session1 = _make_session(events=[_make_event("first")])
        await svc.store_session(session1)
        session2 = _make_session(events=[_make_event("second"), _make_event("third")])
        await svc.store_session(session2)
        assert len(svc._session_events["app/user1"]["session-1"]) == 2
        await svc.close()

    async def test_store_skips_events_without_content(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        event_no_content = Event(id=Event.new_id(), invocation_id="inv-1", author="user")
        session = _make_session(events=[event_no_content, _make_event("valid")])
        await svc.store_session(session)
        assert len(svc._session_events["app/user1"]["session-1"]) == 1
        await svc.close()

    async def test_store_raises_on_non_session(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        with pytest.raises(TypeError, match="Content must be a Session"):
            await svc.store_session("not a session")
        await svc.close()

    async def test_store_different_save_keys(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        s1 = _make_session(events=[_make_event("a")], save_key="key1")
        s2 = _make_session(events=[_make_event("b")], save_key="key2")
        await svc.store_session(s1)
        await svc.store_session(s2)
        assert "key1" in svc._session_events
        assert "key2" in svc._session_events
        await svc.close()

    async def test_store_different_session_ids(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        s1 = _make_session(events=[_make_event("a")], session_id="s1")
        s2 = _make_session(events=[_make_event("b")], session_id="s2")
        await svc.store_session(s1)
        await svc.store_session(s2)
        assert "s1" in svc._session_events["app/user1"]
        assert "s2" in svc._session_events["app/user1"]
        await svc.close()


# ---------------------------------------------------------------------------
# InMemoryMemoryService — search_memory
# ---------------------------------------------------------------------------


class TestInMemorySearchMemory:
    async def test_search_empty_returns_empty(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        result = await svc.search_memory("nonexistent", "query")
        assert isinstance(result, SearchMemoryResponse)
        assert result.memories == []
        await svc.close()

    async def test_search_finds_matching_keyword(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        session = _make_session(events=[_make_event("hello world")])
        await svc.store_session(session)
        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1
        await svc.close()

    async def test_search_no_match(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        session = _make_session(events=[_make_event("hello world")])
        await svc.store_session(session)
        result = await svc.search_memory("app/user1", "zzzzz")
        assert len(result.memories) == 0
        await svc.close()

    async def test_search_respects_limit(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        events = [_make_event(f"hello item {i}") for i in range(5)]
        session = _make_session(events=events)
        await svc.store_session(session)
        result = await svc.search_memory("app/user1", "hello", limit=2)
        assert len(result.memories) == 2
        await svc.close()

    async def test_search_limit_zero_returns_all(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        events = [_make_event(f"hello item {i}") for i in range(5)]
        session = _make_session(events=events)
        await svc.store_session(session)
        result = await svc.search_memory("app/user1", "hello", limit=0)
        assert len(result.memories) == 5
        await svc.close()

    async def test_search_chinese_query(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        session = _make_session(events=[_make_event("你好世界")])
        await svc.store_session(session)
        result = await svc.search_memory("app/user1", "你好")
        assert len(result.memories) == 1
        await svc.close()

    async def test_search_returns_memory_entry_fields(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        session = _make_session(events=[_make_event("hello world", author="assistant")])
        await svc.store_session(session)
        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1
        entry = result.memories[0]
        assert entry.author == "assistant"
        assert entry.timestamp is not None
        assert entry.content is not None
        await svc.close()

    async def test_search_across_sessions(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        s1 = _make_session(events=[_make_event("hello from session1")], session_id="s1")
        s2 = _make_session(events=[_make_event("hello from session2")], session_id="s2")
        await svc.store_session(s1)
        await svc.store_session(s2)
        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 2
        await svc.close()

    async def test_search_updates_ttl_on_match(self):
        cfg = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=0.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        session = _make_session(events=[_make_event("hello world")])
        await svc.store_session(session)
        event_ttl = svc._session_events["app/user1"]["session-1"][0]
        old_update_time = event_ttl.ttl.update_time
        time.sleep(0.01)
        await svc.search_memory("app/user1", "hello")
        assert event_ttl.ttl.update_time >= old_update_time
        await svc.close()

    async def test_search_skips_event_without_text(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        event_empty = Event(
            id=Event.new_id(),
            invocation_id="inv-1",
            author="user",
            content=Content(parts=[Part()]),
        )
        events = [event_empty, _make_event("hello")]
        session = _make_session(events=events)
        await svc.store_session(session)
        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1
        await svc.close()


# ---------------------------------------------------------------------------
# InMemoryMemoryService — cleanup
# ---------------------------------------------------------------------------


class TestInMemoryCleanup:
    async def test_cleanup_expired_removes_old_events(self):
        cfg = _make_config_with_ttl(ttl_seconds=1, cleanup_interval=60.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        session = _make_session(events=[_make_event("old event")])
        await svc.store_session(session)

        for et in svc._session_events["app/user1"]["session-1"]:
            et.ttl = Ttl(enable=True, ttl_seconds=1, cleanup_interval_seconds=60.0)
            et.ttl.update_time = time.time() - 100

        svc._cleanup_expired()
        assert "app/user1" not in svc._session_events
        await svc.close()

    async def test_cleanup_keeps_non_expired_events(self):
        cfg = _make_config_with_ttl(ttl_seconds=9999, cleanup_interval=60.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        session = _make_session(events=[_make_event("fresh event")])
        await svc.store_session(session)
        svc._cleanup_expired()
        assert len(svc._session_events["app/user1"]["session-1"]) == 1
        await svc.close()

    async def test_cleanup_removes_empty_session_dicts(self):
        cfg = _make_config_with_ttl(ttl_seconds=1, cleanup_interval=60.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        session = _make_session(events=[_make_event("old")])
        await svc.store_session(session)

        for et in svc._session_events["app/user1"]["session-1"]:
            et.ttl = Ttl(enable=True, ttl_seconds=1, cleanup_interval_seconds=60.0)
            et.ttl.update_time = time.time() - 100

        svc._cleanup_expired()
        assert "session-1" not in svc._session_events.get("app/user1", {})
        await svc.close()


# ---------------------------------------------------------------------------
# InMemoryMemoryService — cleanup task lifecycle
# ---------------------------------------------------------------------------


class TestInMemoryCleanupTask:
    def test_start_cleanup_no_ttl(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        assert svc._InMemoryMemoryService__cleanup_task is None

    async def test_start_cleanup_with_ttl(self):
        cfg = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        assert svc._InMemoryMemoryService__cleanup_task is not None
        await svc.close()

    async def test_stop_cleanup_task(self):
        cfg = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        svc._stop_cleanup_task()
        assert svc._InMemoryMemoryService__cleanup_task is None
        assert svc._InMemoryMemoryService__cleanup_stop_event is None

    async def test_stop_cleanup_when_no_task(self):
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        svc._stop_cleanup_task()  # should not raise

    async def test_close_stops_cleanup(self):
        cfg = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        await svc.close()
        assert svc._InMemoryMemoryService__cleanup_task is None

    async def test_start_cleanup_idempotent(self):
        cfg = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        task = svc._InMemoryMemoryService__cleanup_task
        svc._start_cleanup_task()
        assert svc._InMemoryMemoryService__cleanup_task is task
        await svc.close()


# ---------------------------------------------------------------------------
# InMemoryMemoryService — init
# ---------------------------------------------------------------------------


class TestInMemoryInit:
    def test_default_init(self):
        svc = InMemoryMemoryService()
        assert svc.enabled is False
        assert svc._session_events == {}

    def test_init_with_config(self):
        cfg = _make_config_no_ttl()
        svc = InMemoryMemoryService(memory_service_config=cfg)
        assert svc.enabled is True
        assert svc._memory_service_config is cfg


# ---------------------------------------------------------------------------
# InMemoryMemoryService — _cleanup_loop
# ---------------------------------------------------------------------------


class TestInMemoryCleanupLoop:
    async def test_cleanup_loop_runs_and_stops(self):
        cfg = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=0.05)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        await asyncio.sleep(0.1)
        await svc.close()

    async def test_cleanup_loop_handles_cleanup_error(self):
        cfg = _make_config_with_ttl(ttl_seconds=3600, cleanup_interval=0.05)
        svc = InMemoryMemoryService(memory_service_config=cfg)
        with patch.object(svc, "_cleanup_expired", side_effect=RuntimeError("boom")):
            await asyncio.sleep(0.1)
        await svc.close()

    async def test_search_skips_event_without_content_or_parts(self):
        """Cover the continue branch in search_memory for events without content."""
        svc = InMemoryMemoryService(memory_service_config=_make_config_no_ttl())
        session = _make_session(events=[_make_event("hello")])
        await svc.store_session(session)
        event_no_content = EventTtl(
            event=Event(id=Event.new_id(), invocation_id="inv-1", author="user"),
            ttl=Ttl(enable=False),
        )
        svc._session_events["app/user1"]["session-1"].insert(0, event_no_content)
        result = await svc.search_memory("app/user1", "hello")
        assert len(result.memories) == 1
        await svc.close()
