# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.sessions._redis_session_service.

Covers:
- RedisSessionService: create_session, get_session, list_sessions, delete_session,
  append_event, update_session, close, TTL refresh
All Redis I/O is mocked via RedisStorage.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._redis_session_service import RedisSessionService
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, EventActions, Part, State


def _make_config(ttl_seconds=0, cleanup_interval=0.0, enable_ttl=False):
    config = SessionServiceConfig()
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


def _make_session_obj(**kwargs):
    defaults = dict(id="s1", app_name="app", user_id="user", save_key="app/user", last_update_time=time.time())
    defaults.update(kwargs)
    return Session(**defaults)


class _MockRedisStorage:
    """Mock RedisStorage that stores data in memory."""

    def __init__(self):
        self._store = {}
        self._hash_store = {}

    @asynccontextmanager
    async def create_db_session(self):
        yield MagicMock()

    async def execute_command(self, session, command):
        method = command.method
        args = command.args

        if method == 'set':
            key, value = args[0], args[1]
            self._store[key] = value
            return True
        elif method == 'get':
            key = args[0]
            return self._store.get(key)
        elif method == 'keys':
            pattern = args[0]
            prefix = pattern.replace("*", "")
            return [k for k in self._store.keys() if k.startswith(prefix)]
        elif method == 'hset':
            key = args[0]
            pairs = args[1:]
            if key not in self._hash_store:
                self._hash_store[key] = {}
            for i in range(0, len(pairs), 2):
                self._hash_store[key][pairs[i]] = pairs[i + 1]
            return True
        elif method == 'hgetall':
            key = args[0]
            return self._hash_store.get(key, {})
        return None

    async def delete(self, session, key):
        self._store.pop(key, None)

    async def expire(self, session, expire_obj):
        pass

    async def close(self):
        pass


def _create_service(config=None):
    """Create a RedisSessionService with mocked storage."""
    config = config or _make_config()
    with patch("trpc_agent_sdk.sessions._redis_session_service.RedisStorage"):
        svc = RedisSessionService(db_url="redis://localhost:6379", session_config=config)
    svc._redis_storage = _MockRedisStorage()
    return svc


class TestRedisCreateSession:
    async def test_create_basic(self):
        svc = _create_service()
        session = await svc.create_session(app_name="app", user_id="user")
        assert session.app_name == "app"
        assert session.user_id == "user"
        assert session.id is not None
        await svc.close()

    async def test_create_with_custom_id(self):
        svc = _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="custom-id")
        assert session.id == "custom-id"
        await svc.close()

    async def test_create_with_state(self):
        svc = _create_service()
        session = await svc.create_session(
            app_name="app", user_id="user",
            state={
                "sk": "sv",
                f"{State.APP_PREFIX}ak": "av",
                f"{State.USER_PREFIX}uk": "uv",
            })
        assert session.state["sk"] == "sv"
        assert session.state[f"{State.APP_PREFIX}ak"] == "av"
        assert session.state[f"{State.USER_PREFIX}uk"] == "uv"
        await svc.close()

    async def test_create_with_whitespace_id(self):
        svc = _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="  ")
        assert session.id.strip() == session.id
        assert len(session.id) > 0
        await svc.close()


class TestRedisGetSession:
    async def test_get_existing(self):
        svc = _create_service()
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result is not None
        assert result.id == "s1"
        await svc.close()

    async def test_get_nonexistent(self):
        svc = _create_service()
        result = await svc.get_session(app_name="app", user_id="user", session_id="nonexistent")
        assert result is None
        await svc.close()

    async def test_get_with_merged_state(self):
        svc = _create_service()
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


class TestRedisListSessions:
    async def test_list_empty(self):
        svc = _create_service()
        result = await svc.list_sessions(app_name="app", user_id="user")
        assert result.sessions == []
        await svc.close()

    async def test_list_multiple(self):
        svc = _create_service()
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        await svc.create_session(app_name="app", user_id="user", session_id="s2")
        result = await svc.list_sessions(app_name="app", user_id="user")
        assert len(result.sessions) == 2
        await svc.close()

    async def test_list_sessions_have_no_events(self):
        svc = _create_service()
        created = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event()
        await svc.append_event(created, event)
        result = await svc.list_sessions(app_name="app", user_id="user")
        for s in result.sessions:
            assert s.events == []
        await svc.close()


class TestRedisDeleteSession:
    async def test_delete_existing(self):
        svc = _create_service()
        await svc.create_session(app_name="app", user_id="user", session_id="s1")
        await svc.delete_session(app_name="app", user_id="user", session_id="s1")
        result = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert result is None
        await svc.close()

    async def test_delete_nonexistent(self):
        svc = _create_service()
        await svc.delete_session(app_name="app", user_id="user", session_id="nonexistent")
        await svc.close()


class TestRedisAppendEvent:
    async def test_append_basic(self):
        svc = _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 1
        await svc.close()

    async def test_append_partial_skipped(self):
        svc = _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        event = _make_event(partial=True)
        await svc.append_event(session, event)
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert len(stored.events) == 0
        await svc.close()

    async def test_append_with_state_delta(self):
        svc = _create_service()
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
        svc = _create_service()
        session = _make_session_obj(id="nonexistent")
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        await svc.close()


class TestRedisUpdateSession:
    async def test_update_existing(self):
        svc = _create_service()
        session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
        session.state["new_key"] = "new_val"
        await svc.update_session(session)
        stored = await svc.get_session(app_name="app", user_id="user", session_id="s1")
        assert stored.state.get("new_key") == "new_val"
        await svc.close()

    async def test_update_nonexistent(self):
        svc = _create_service()
        session = _make_session_obj(id="nonexistent")
        await svc.update_session(session)
        await svc.close()


class TestRedisRefreshTtl:
    async def test_refresh_ttl_disabled(self):
        svc = _create_service(config=_make_config())
        mock_redis = MagicMock()
        await svc._refresh_ttl(mock_redis, "some_key")
        await svc.close()

    async def test_refresh_ttl_enabled(self):
        config = _make_config(enable_ttl=True, ttl_seconds=3600, cleanup_interval=60.0)
        svc = _create_service(config=config)
        mock_redis = MagicMock()
        svc._redis_storage.expire = AsyncMock()
        await svc._refresh_ttl(mock_redis, "some_key")
        svc._redis_storage.expire.assert_called_once()
        await svc.close()


class TestRedisClose:
    async def test_close(self):
        svc = _create_service()
        await svc.close()
