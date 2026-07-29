# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.sessions._in_memory_session_service.

Covers:
- SessionWithTTL: update, get, expiration
- StateWithTTL: update, get, expiration
- InMemorySessionService: create_session, get_session, list_sessions, delete_session,
  append_event, update_session, state management, cleanup, close
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._in_memory_session_service import (
    InMemorySessionService,
    SessionWithTTL,
    StateWithTTL,
)
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, EventActions, Part, State, Ttl


def _make_session_config(ttl_seconds=0, cleanup_interval=0.0, enable_ttl=False, **kwargs):
    config = SessionServiceConfig(**kwargs)
    if enable_ttl:
        config.ttl = SessionServiceConfig.create_ttl_config(enable=True,
                                                            ttl_seconds=ttl_seconds,
                                                            cleanup_interval_seconds=cleanup_interval)
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


# ---------------------------------------------------------------------------
# SessionWithTTL
# ---------------------------------------------------------------------------


class TestSessionWithTTL:

    def test_update_and_get(self):
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        wrapper = SessionWithTTL(session=session)
        new_session = Session(id="s2", app_name="app", user_id="user", save_key="k")
        wrapper.update(new_session)
        assert wrapper.get().id == "s2"

    def test_get_expired_returns_none(self):
        ttl = Ttl(enable=True, ttl_seconds=1, cleanup_interval_seconds=60.0)
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        wrapper = SessionWithTTL(session=session, ttl=ttl)
        wrapper.ttl.update_time = time.time() - 100
        result = wrapper.get()
        assert result is None

    def test_get_non_expired(self):
        ttl = Ttl(enable=True, ttl_seconds=9999, cleanup_interval_seconds=60.0)
        session = Session(id="s1", app_name="app", user_id="user", save_key="k")
        wrapper = SessionWithTTL(session=session, ttl=ttl)
        result = wrapper.get()
        assert result is not None
        assert result.id == "s1"


# ---------------------------------------------------------------------------
# StateWithTTL
# ---------------------------------------------------------------------------


class TestStateWithTTL:

    def test_update(self):
        wrapper = StateWithTTL()
        result = wrapper.update({"key": "value"})
        assert result == {"key": "value"}

    def test_get(self):
        wrapper = StateWithTTL(data={"key": "value"})
        assert wrapper.get() == {"key": "value"}

    def test_update_merges(self):
        wrapper = StateWithTTL(data={"a": 1})
        result = wrapper.update({"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_get_expired_returns_empty(self):
        ttl = Ttl(enable=True, ttl_seconds=1, cleanup_interval_seconds=60.0)
        wrapper = StateWithTTL(data={"key": "value"}, ttl=ttl)
        wrapper.ttl.update_time = time.time() - 100
        assert wrapper.get() == {}

    def test_update_expired_resets_then_updates(self):
        ttl = Ttl(enable=True, ttl_seconds=1, cleanup_interval_seconds=60.0)
        wrapper = StateWithTTL(data={"old": "data"}, ttl=ttl)
        wrapper.ttl.update_time = time.time() - 100
        result = wrapper.update({"new": "data"})
        assert result == {"new": "data"}
        assert "old" not in result


# ---------------------------------------------------------------------------
# InMemorySessionService — create_session
# ---------------------------------------------------------------------------


class TestInMemoryCreateSession:

    async def test_create_basic_session(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user")
        assert session.app_name == "app"
        assert session.user_id == "user"
        assert session.id is not None
        await svc.close()

    async def test_create_with_custom_id(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", session_id="custom-id")
        assert session.id == "custom-id"
        await svc.close()

    async def test_create_with_whitespace_id_generates_uuid(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", session_id="   ")
        assert len(session.id) > 0
        assert session.id.strip() == session.id
        await svc.close()

    async def test_create_with_state(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app",
                                           user_id="user",
                                           state={
                                               "session_key": "session_val",
                                               f"{State.APP_PREFIX}app_key": "app_val",
                                               f"{State.USER_PREFIX}user_key": "user_val",
                                           })
        assert session.state["session_key"] == "session_val"
        assert session.state[f"{State.APP_PREFIX}app_key"] == "app_val"
        assert session.state[f"{State.USER_PREFIX}user_key"] == "user_val"
        await svc.close()

    async def test_create_returns_deep_copy(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", state={"key": "val"})
        session.state["key"] = "modified"
        stored = await svc.get_session(app_name="app", user_id="user", session_id=session.id)
        assert stored.state["key"] == "val"
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — get_session
# ---------------------------------------------------------------------------


class TestInMemoryGetSession:

    async def test_get_existing_session(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        created = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result is not None
        assert result.id == "s1"
        await svc.close()

    async def test_get_nonexistent_session(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        result = await svc.get_session(app_name="app", user_id="user", session_id="nonexistent")
        assert result is None
        await svc.close()

    async def test_get_returns_merged_state(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app",
                                 user_id="user",
                                 session_id="s1",
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

    async def test_get_returns_deep_copy(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        s1 = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        s1.state["mutated"] = True
        s2 = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert "mutated" not in s2.state
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — list_sessions
# ---------------------------------------------------------------------------


class TestInMemoryListSessions:

    async def test_list_empty(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        result = await svc.list_sessions(app_name="app", user_id="user")
        assert result.sessions == []
        await svc.close()

    async def test_list_multiple(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        await svc.create_session(app_name="app", user_id="user", session_id="s2")
        result = await svc.list_sessions(app_name="app", user_id="user")
        assert len(result.sessions) == 2
        await svc.close()

    async def test_list_sessions_have_no_events(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event()
        await svc.append_event(session, event)
        result = await svc.list_sessions(app_name="app", user_id="user")
        assert len(result.sessions) == 1
        assert result.sessions[0].events == []
        assert result.sessions[0].historical_events == []
        await svc.close()

    async def test_list_nonexistent_app(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        result = await svc.list_sessions(app_name="nonexistent", user_id="user")
        assert result.sessions == []
        await svc.close()

    async def test_list_nonexistent_user(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user1", session_id="s1")
        result = await svc.list_sessions(app_name="app", user_id="user2")
        assert result.sessions == []
        await svc.close()

    async def test_list_all_users_when_user_id_none(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user1", session_id="s1")
        await svc.create_session(app_name="app", user_id="user2", session_id="s2")
        result = await svc.list_sessions(app_name="app", user_id=None)
        ids = sorted(s.id for s in result.sessions)
        assert ids == ["s1", "s2"]
        await svc.close()

    async def test_list_all_users_filtered_by_app(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app1", user_id="user1", session_id="s1")
        await svc.create_session(app_name="app2", user_id="user1", session_id="s2")
        result = await svc.list_sessions(app_name="app1", user_id=None)
        assert [s.id for s in result.sessions] == ["s1"]
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — delete_session
# ---------------------------------------------------------------------------


class TestInMemoryDeleteSession:

    async def test_delete_existing(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        await svc.delete_session(app_name="app", user_id="user", session_id="s1")
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result is None
        await svc.close()

    async def test_delete_nonexistent(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.delete_session(app_name="app", user_id="user", session_id="nonexistent")
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — append_event
# ---------------------------------------------------------------------------


class TestInMemoryAppendEvent:

    async def test_append_basic(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 1
        await svc.close()

    async def test_append_partial_skipped(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event(partial=True)
        await svc.append_event(session, event)
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 0
        await svc.close()

    async def test_append_with_state_delta(self):
        svc = InMemorySessionService(session_config=_make_session_config())
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

    async def test_append_to_nonexistent_app(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = Session(id="s1", app_name="nonexistent", user_id="user", save_key="k")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        await svc.close()

    async def test_append_to_nonexistent_user(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user1", session_id="s1")
        session = Session(id="s1", app_name="app", user_id="nonexistent", save_key="k")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        await svc.close()

    async def test_append_to_nonexistent_session(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        session = Session(id="nonexistent", app_name="app", user_id="user", save_key="k")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        await svc.close()

    async def test_append_updates_conversation_count(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        session.conversation_count = 5
        event = _make_event()
        await svc.append_event(session, event)
        stored_session = svc._get_session("app", "user", "s1")
        assert stored_session.conversation_count == 5
        await svc.close()

    @pytest.mark.parametrize("store_historical_events", [False, True])
    async def test_append_mirrors_filtered_event_windows_to_storage(self, store_historical_events):
        """Keep stored active/history windows aligned after max-events filtering.

        max_events 过滤后，确保存储中的活动/历史事件窗口与调用方 Session 一致。
        """
        config = _make_session_config(
            max_events=2,
            store_historical_events=store_historical_events,
        )
        svc = InMemorySessionService(session_config=config)
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")

        # A user event acts as the retained conversation anchor while later
        # agent events force max_events filtering on both event windows.
        # user 事件作为保留锚点，后续 agent 事件触发两个事件窗口的 max_events 过滤。
        for index in range(6):
            event = _make_event(
                author="user" if index == 2 else "agent",
                text=f"message-{index}",
            )
            event.id = f"event-{index}"
            await svc.append_event(session, event)

        expected_active_ids = ["event-2", "event-5"]
        expected_historical_ids = (["event-0", "event-1", "event-3", "event-4"] if store_historical_events else [])
        assert [event.id for event in session.events] == expected_active_ids
        assert [event.id for event in session.historical_events] == expected_historical_ids

        # Inspect the raw stored snapshot so get_session read-view filtering
        # cannot conceal persistence-window drift.
        # 直接检查原始存储快照，避免 get_session 的读取过滤掩盖持久化窗口漂移。
        stored_session = svc._get_session("app", "user", "s1")
        assert [event.id for event in stored_session.events] == expected_active_ids
        assert [event.id for event in stored_session.historical_events] == expected_historical_ids
        # Copy the list containers without sharing caller-owned windows.
        # 复制列表容器，避免与调用方共享可变的事件窗口。
        assert stored_session.events is not session.events
        assert stored_session.historical_events is not session.historical_events
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — update_session
# ---------------------------------------------------------------------------


class TestInMemoryUpdateSession:

    async def test_update_existing(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        session.state["new_key"] = "new_value"
        await svc.update_session(session)
        await svc.close()

    async def test_append_after_update_does_not_share_event_list_with_storage(self):
        """Verify update_session stores a deep-isolated event list.

        验证 update_session 保存的是深度隔离副本，后续追加不会因调用方与存储
        共享 events 列表而产生重复事件。
        """
        config = _make_session_config(store_historical_events=True)
        svc = InMemorySessionService(session_config=config)
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        first = _make_event(text="first")
        first.id = "event-1"
        second = _make_event(text="second")
        second.id = "event-2"

        # Persist an initial event and replace storage with the session snapshot.
        # 先持久化首个事件，再用当前 Session 快照更新存储。
        await svc.append_event(session, first)
        await svc.update_session(session)
        # If update_session retained a shared list, this append would appear twice.
        # 若 update_session 保留了共享列表，此次追加会在存储中出现两次。
        await svc.append_event(session, second)

        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        # Reload storage to prove both IDs exist exactly once and in order.
        # 重新读取存储，确认两个 ID 均只出现一次且顺序正确。
        assert [event.id for event in stored.events] == ["event-1", "event-2"]
        await svc.close()

    async def test_update_nonexistent_app(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        session = Session(id="s1", app_name="nonexistent", user_id="user", save_key="k")
        await svc.update_session(session)
        await svc.close()

    async def test_update_nonexistent_user(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        session = Session(id="s1", app_name="app", user_id="nonexistent", save_key="k")
        await svc.update_session(session)
        await svc.close()

    async def test_update_nonexistent_session(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        session = Session(id="nonexistent", app_name="app", user_id="user", save_key="k")
        await svc.update_session(session)
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — cleanup
# ---------------------------------------------------------------------------


class TestInMemoryCleanupExpired:

    async def test_cleanup_removes_expired_sessions(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=1, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        await svc.create_session(app_name="app", user_id="user", session_id="s1")

        for app_sessions in svc._sessions.values():
            for user_sessions in app_sessions.values():
                for s_ttl in user_sessions.values():
                    s_ttl.ttl.update_time = time.time() - 100

        svc._cleanup_expired()
        assert "app" not in svc._sessions
        await svc.close()

    async def test_cleanup_removes_expired_app_state(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=1, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        await svc.create_session(app_name="app", user_id="user", state={f"{State.APP_PREFIX}k": "v"})
        svc._app_state["app"].ttl.update_time = time.time() - 100
        svc._cleanup_expired()
        assert "app" not in svc._app_state
        await svc.close()

    async def test_cleanup_removes_expired_user_state(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=1, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        await svc.create_session(app_name="app", user_id="user", state={f"{State.USER_PREFIX}k": "v"})
        svc._user_state["app"]["user"].ttl.update_time = time.time() - 100
        svc._cleanup_expired()
        assert "app" not in svc._user_state
        await svc.close()

    async def test_cleanup_keeps_non_expired(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=9999, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        svc._cleanup_expired()
        assert svc._get_session("app", "user", "s1") is not None
        await svc.close()

    async def test_cleanup_nothing_expired(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=9999, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        svc._cleanup_expired()
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — cleanup task lifecycle
# ---------------------------------------------------------------------------


class TestInMemoryCleanupTask:

    def test_no_cleanup_task_when_ttl_disabled(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        assert svc._InMemorySessionService__cleanup_task is None

    async def test_cleanup_task_created_with_ttl(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        assert svc._InMemorySessionService__cleanup_task is not None
        await svc.close()

    async def test_stop_cleanup_task(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        svc._stop_cleanup_task()
        assert svc._InMemorySessionService__cleanup_task is None
        assert svc._InMemorySessionService__cleanup_stop_event is None

    async def test_stop_cleanup_when_no_task(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        svc._stop_cleanup_task()

    async def test_close_stops_cleanup(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        await svc.close()
        assert svc._InMemorySessionService__cleanup_task is None

    async def test_start_cleanup_idempotent(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=3600.0)
        svc = InMemorySessionService(session_config=config)
        task = svc._InMemorySessionService__cleanup_task
        svc._start_cleanup_task()
        assert svc._InMemorySessionService__cleanup_task is task
        await svc.close()

    async def test_cleanup_loop_runs_and_stops(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=0.05)
        svc = InMemorySessionService(session_config=config)
        await asyncio.sleep(0.15)
        await svc.close()

    async def test_cleanup_loop_handles_error(self):
        config = _make_session_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=0.05)
        svc = InMemorySessionService(session_config=config)
        with patch.object(svc, "_cleanup_expired", side_effect=RuntimeError("test")):
            await asyncio.sleep(0.15)
        await svc.close()


# ---------------------------------------------------------------------------
# InMemorySessionService — internal helpers
# ---------------------------------------------------------------------------


class TestInMemoryInternalHelpers:

    async def test_get_app_state_nonexistent(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        assert svc._get_app_state("nonexistent") == {}
        await svc.close()

    async def test_get_user_state_nonexistent_app(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        assert svc._get_user_state("nonexistent", "user") == {}
        await svc.close()

    async def test_get_user_state_nonexistent_user(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        svc._user_state["app"] = {}
        assert svc._get_user_state("app", "nonexistent") == {}
        await svc.close()

    async def test_get_session_nonexistent(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        assert svc._get_session("a", "b", "c") is None
        await svc.close()

    async def test_is_session_exist_false(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        assert svc._is_session_exist("a", "b", "c") is False
        await svc.close()

    async def test_is_session_exist_true(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        assert svc._is_session_exist("app", "user", "s1") is True
        await svc.close()

    async def test_update_app_state_new(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        result = svc._update_app_state("app", {"key": "val"})
        assert result == {"key": "val"}
        await svc.close()

    async def test_update_app_state_existing(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        svc._update_app_state("app", {"k1": "v1"})
        result = svc._update_app_state("app", {"k2": "v2"})
        assert result == {"k1": "v1", "k2": "v2"}
        await svc.close()

    async def test_update_user_state_new(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        result = svc._update_user_state("app", "user", {"key": "val"})
        assert result == {"key": "val"}
        await svc.close()

    async def test_update_user_state_existing(self):
        svc = InMemorySessionService(session_config=_make_session_config())
        svc._update_user_state("app", "user", {"k1": "v1"})
        result = svc._update_user_state("app", "user", {"k2": "v2"})
        assert result == {"k1": "v1", "k2": "v2"}
        await svc.close()
