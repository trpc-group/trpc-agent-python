# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.sessions._sql_session_service.

Covers:
- StorageSession: to_session
- SessionStorageEvent: from_event, to_event, long_running_tool_ids property
- SqlSessionService: create_session, get_session, list_sessions, delete_session,
  append_event, update_session, state management, cleanup, close
Uses in-memory SQLite for integration-style tests.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._sql_session_service import (
    SessionStorageEvent,
    SqlSessionService,
    StorageSession,
)
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, EventActions, FunctionCall, Part, State


def _make_config(ttl_seconds=0, cleanup_interval=0.0, enable_ttl=False, num_recent_events=0):
    config = SessionServiceConfig(num_recent_events=num_recent_events)
    if enable_ttl:
        config.ttl = SessionServiceConfig.create_ttl_config(
            enable=True, ttl_seconds=ttl_seconds, cleanup_interval_seconds=cleanup_interval)
    else:
        config.clean_ttl_config()
    return config


def _make_event(author="agent", text="hello", state_delta=None, partial=False):
    actions = EventActions(state_delta=state_delta) if state_delta else EventActions()
    return Event(
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        actions=actions,
        partial=partial,
    )


def _make_event_with_function_call():
    fc = FunctionCall(name="tool", args={"key": "val"})
    return Event(
        invocation_id="inv-1",
        author="agent",
        content=Content(parts=[Part(function_call=fc)]),
    )


async def _create_service(config=None):
    config = config or _make_config()
    svc = SqlSessionService(db_url="sqlite:///:memory:", session_config=config, is_async=False)
    with patch('trpc_agent_sdk.storage._sql.event.listen'):
        await svc._sql_storage.create_sql_engine()
    return svc


# ---------------------------------------------------------------------------
# SessionStorageEvent — from_event / to_event
# ---------------------------------------------------------------------------

class TestSessionStorageEvent:
    def test_from_event_basic(self):
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        event = _make_event(author="user", text="hello world")
        storage_event = SessionStorageEvent.from_event(session, event)
        assert storage_event.id == event.id
        assert storage_event.author == "user"
        assert storage_event.app_name == "app"
        assert storage_event.session_id == "s1"

    def test_from_event_with_function_call(self):
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        event = _make_event_with_function_call()
        storage_event = SessionStorageEvent.from_event(session, event)
        assert storage_event.content is not None

    def test_from_event_no_content(self):
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        event = Event(invocation_id="inv-1", author="agent", actions=EventActions())
        storage_event = SessionStorageEvent.from_event(session, event)
        assert storage_event.content is None

    def test_long_running_tool_ids_property(self):
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        event = _make_event()
        event.long_running_tool_ids = {"tool-1", "tool-2"}
        storage_event = SessionStorageEvent.from_event(session, event)
        assert storage_event.long_running_tool_ids == {"tool-1", "tool-2"}

    def test_long_running_tool_ids_none(self):
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        event = _make_event()
        storage_event = SessionStorageEvent.from_event(session, event)
        storage_event.long_running_tool_ids = None
        assert storage_event.long_running_tool_ids_json is None

    def test_long_running_tool_ids_empty(self):
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        event = _make_event()
        event.long_running_tool_ids = set()
        storage_event = SessionStorageEvent.from_event(session, event)
        ids = storage_event.long_running_tool_ids
        assert ids == set()


# ---------------------------------------------------------------------------
# SqlSessionService — create_session
# ---------------------------------------------------------------------------

class TestSqlCreateSession:
    async def test_create_basic(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user")
        assert session.app_name == "app"
        assert session.user_id == "user"
        assert session.id is not None
        await svc.close()

    async def test_create_with_custom_id(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="custom-id")
        assert session.id == "custom-id"
        await svc.close()

    async def test_create_with_state(self):
        svc = await _create_service()
        session = await svc.create_session(
            app_name="app", user_id="user", session_id="s1",
            state={
                "sk": "sv",
                f"{State.APP_PREFIX}ak": "av",
                f"{State.USER_PREFIX}uk": "uv",
            })
        assert session.state["sk"] == "sv"
        assert session.state[f"{State.APP_PREFIX}ak"] == "av"
        assert session.state[f"{State.USER_PREFIX}uk"] == "uv"
        await svc.close()

    async def test_create_existing_session_updates(self):
        svc = await _create_service()
        s1 = await svc.create_session(app_name="app", user_id="user", session_id="s1", state={"k1": "v1"})
        s2 = await svc.create_session(app_name="app", user_id="user", session_id="s1", state={"k2": "v2"})
        assert s2.state.get("k2") == "v2"
        await svc.close()

    async def test_create_with_whitespace_id(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="  ")
        assert len(session.id) > 0
        assert session.id.strip() == session.id
        await svc.close()


# ---------------------------------------------------------------------------
# SqlSessionService — get_session
# ---------------------------------------------------------------------------

class TestSqlGetSession:
    async def test_get_existing(self):
        svc = await _create_service()
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result is not None
        assert result.id == "s1"
        await svc.close()

    async def test_get_nonexistent(self):
        svc = await _create_service()
        result = await svc.get_session(app_name="app", user_id="user", session_id="nonexistent")
        assert result is None
        await svc.close()

    async def test_get_with_events(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event(text="test event")
        await svc.append_event(session, event)
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(result.events) == 1
        await svc.close()

    async def test_get_with_num_recent_events(self):
        config = _make_config(num_recent_events=2)
        svc = await _create_service(config=config)
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        for i in range(5):
            event = _make_event(text=f"msg{i}")
            await svc.append_event(session, event)
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(result.events) <= 2
        await svc.close()

    async def test_get_with_merged_state(self):
        svc = await _create_service()
        await svc.create_session(
            app_name="app", user_id="user", session_id="s1",
            state={
                "sk": "sv",
                f"{State.APP_PREFIX}ak": "av",
                f"{State.USER_PREFIX}uk": "uv",
            })
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result.state["sk"] == "sv"
        assert result.state[f"{State.APP_PREFIX}ak"] == "av"
        assert result.state[f"{State.USER_PREFIX}uk"] == "uv"
        await svc.close()


# ---------------------------------------------------------------------------
# SqlSessionService — list_sessions
# ---------------------------------------------------------------------------

class TestSqlListSessions:
    async def test_list_empty(self):
        svc = await _create_service()
        result = await svc.list_sessions(app_name="app", user_id="user")
        assert result.sessions == []
        await svc.close()

    async def test_list_multiple(self):
        svc = await _create_service()
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        await svc.create_session(app_name="app", user_id="user", session_id="s2")
        result = await svc.list_sessions(app_name="app", user_id="user")
        assert len(result.sessions) == 2
        await svc.close()

    async def test_list_sessions_have_no_events(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event()
        await svc.append_event(session, event)
        result = await svc.list_sessions(app_name="app", user_id="user")
        for s in result.sessions:
            assert s.events == []
        await svc.close()


# ---------------------------------------------------------------------------
# SqlSessionService — delete_session
# ---------------------------------------------------------------------------

class TestSqlDeleteSession:
    async def test_delete_existing(self):
        svc = await _create_service()
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        await svc.delete_session(app_name="app", user_id="user", session_id="s1")
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result is None
        await svc.close()


# ---------------------------------------------------------------------------
# SqlSessionService — append_event
# ---------------------------------------------------------------------------

class TestSqlAppendEvent:
    async def test_append_basic(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 1
        await svc.close()

    async def test_append_partial_skipped(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event(partial=True)
        await svc.append_event(session, event)
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 0
        await svc.close()

    async def test_append_with_state_delta(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event(state_delta={
            "session_key": "sv",
            f"{State.APP_PREFIX}app_key": "av",
            f"{State.USER_PREFIX}user_key": "uv",
        })
        await svc.append_event(session, event)
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert stored.state["session_key"] == "sv"
        assert stored.state[f"{State.APP_PREFIX}app_key"] == "av"
        assert stored.state[f"{State.USER_PREFIX}user_key"] == "uv"
        await svc.close()

    async def test_append_to_nonexistent_session(self):
        svc = await _create_service()
        session = Session(id="nonexistent", app_name="app", user_id="user", save_key="k")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        await svc.close()

    async def test_append_multiple_events(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        for i in range(5):
            event = _make_event(text=f"msg{i}")
            await svc.append_event(session, event)
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 5
        await svc.close()


# ---------------------------------------------------------------------------
# SqlSessionService — update_session
# ---------------------------------------------------------------------------

class TestSqlUpdateSession:
    async def test_update_existing(self):
        svc = await _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event(text="original event")
        await svc.append_event(session, event)
        session.events = []
        await svc.update_session(session)
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 0
        await svc.close()

    async def test_update_nonexistent(self):
        svc = await _create_service()
        session = Session(id="nonexistent", app_name="app", user_id="user", save_key="k")
        await svc.update_session(session)
        await svc.close()


# ---------------------------------------------------------------------------
# SqlSessionService — cleanup
# ---------------------------------------------------------------------------

class TestSqlCleanup:
    def test_no_cleanup_task_when_disabled(self):
        config = _make_config()
        with patch("trpc_agent_sdk.sessions._sql_session_service.SqlStorage"):
            svc = SqlSessionService(db_url="sqlite:///:memory:", session_config=config)
        assert svc._SqlSessionService__cleanup_task is None

    async def test_cleanup_task_created(self):
        config = _make_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=3600.0)
        svc = await _create_service(config=config)
        assert svc._SqlSessionService__cleanup_task is not None
        await svc.close()

    async def test_stop_cleanup(self):
        config = _make_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=3600.0)
        svc = await _create_service(config=config)
        svc._stop_cleanup_task()
        assert svc._SqlSessionService__cleanup_task is None
        await svc.close()

    async def test_stop_cleanup_when_no_task(self):
        svc = await _create_service()
        svc._stop_cleanup_task()
        await svc.close()

    async def test_close_stops_cleanup(self):
        config = _make_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=3600.0)
        svc = await _create_service(config=config)
        await svc.close()
        assert svc._SqlSessionService__cleanup_task is None

    async def test_cleanup_expired_async(self):
        config = _make_config(enable_ttl=True, ttl_seconds=1, cleanup_interval=3600.0)
        svc = await _create_service(config=config)
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        await asyncio.sleep(2)
        await svc._cleanup_expired_async()
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result is None
        await svc.close()

    async def test_cleanup_loop_runs_and_stops(self):
        config = _make_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=0.05)
        svc = await _create_service(config=config)
        await asyncio.sleep(0.15)
        await svc.close()
