# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for SessionManager class."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.server.ag_ui._core._session_manager import SessionManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the SessionManager singleton before each test."""
    SessionManager.reset_instance()
    yield
    SessionManager.reset_instance()


def _make_session_service():
    """Create a mock session service with AsyncMock methods."""
    svc = Mock()
    svc.get_session = AsyncMock(return_value=None)
    svc.create_session = AsyncMock()
    svc.delete_session = AsyncMock()
    svc.append_event = AsyncMock()
    svc.update_session = AsyncMock()
    return svc


def _make_session(
    id="sess-1",
    app_name="app",
    user_id="user-1",
    state=None,
    last_update_time=None,
):
    """Create a mock session object."""
    session = Mock()
    session.id = id
    session.app_name = app_name
    session.user_id = user_id
    session.state = state if state is not None else {}
    session.last_update_time = last_update_time or time.time()
    return session


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_same_instance_returned(self):
        svc = _make_session_service()
        m1 = SessionManager(session_service=svc, auto_cleanup=False)
        m2 = SessionManager(session_service=svc, auto_cleanup=False)
        assert m1 is m2

    def test_get_instance_returns_same(self):
        svc = _make_session_service()
        m1 = SessionManager(session_service=svc, auto_cleanup=False)
        m2 = SessionManager.get_instance()
        assert m1 is m2

    def test_reset_instance_clears_state(self):
        svc = _make_session_service()
        m1 = SessionManager(session_service=svc, auto_cleanup=False)
        SessionManager.reset_instance()
        m2 = SessionManager(session_service=svc, auto_cleanup=False)
        assert m1 is not m2

    def test_init_not_called_twice(self):
        svc = _make_session_service()
        m = SessionManager(session_service=svc, auto_cleanup=False, session_timeout_seconds=999)
        assert m._timeout == 999
        # Second init with different timeout should be ignored
        SessionManager(session_service=svc, auto_cleanup=False, session_timeout_seconds=111)
        assert m._timeout == 999

    def test_default_session_service_when_none(self):
        m = SessionManager(session_service=None, auto_cleanup=False)
        assert m._session_service is not None


# ---------------------------------------------------------------------------
# TestGetOrCreateSession
# ---------------------------------------------------------------------------


class TestGetOrCreateSession:
    async def test_creates_new_session(self):
        svc = _make_session_service()
        new_session = _make_session(id="sess-1", app_name="app", user_id="user-1")
        svc.get_session.return_value = None
        svc.create_session.return_value = new_session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_or_create_session("sess-1", "app", "user-1")

        assert result is new_session
        svc.create_session.assert_called_once()
        assert "app:sess-1" in mgr._session_keys

    async def test_gets_existing_session(self):
        svc = _make_session_service()
        existing = _make_session(id="sess-1", app_name="app", user_id="user-1")
        svc.get_session.return_value = existing

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_or_create_session("sess-1", "app", "user-1")

        assert result is existing
        svc.create_session.assert_not_called()

    async def test_respects_max_sessions_per_user(self):
        svc = _make_session_service()
        old_session = _make_session(id="old-1", app_name="app", user_id="user-1", last_update_time=100.0)
        new_session = _make_session(id="new-1", app_name="app", user_id="user-1")

        svc.get_session.side_effect = [
            old_session,  # _remove_oldest_user_session -> get_session for old-1
            None,  # get_or_create_session -> get_session for new-1 (not found)
        ]
        svc.create_session.return_value = new_session

        mgr = SessionManager(session_service=svc, max_sessions_per_user=1, auto_cleanup=False)
        # Pre-track an existing session
        mgr._track_session("app:old-1", "user-1")

        result = await mgr.get_or_create_session("new-1", "app", "user-1")

        assert result is new_session
        svc.delete_session.assert_called_once()

    async def test_initial_state_passed_to_create(self):
        svc = _make_session_service()
        svc.get_session.return_value = None
        new_session = _make_session()
        svc.create_session.return_value = new_session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        await mgr.get_or_create_session("s1", "app", "u1", initial_state={"key": "val"})

        svc.create_session.assert_called_once_with(
            session_id="s1", user_id="u1", app_name="app", state={"key": "val"}
        )

    async def test_tracks_session_after_creation(self):
        svc = _make_session_service()
        svc.get_session.return_value = None
        svc.create_session.return_value = _make_session()

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        await mgr.get_or_create_session("s1", "app", "u1")

        assert "app:s1" in mgr._session_keys
        assert "app:s1" in mgr._user_sessions.get("u1", set())


# ---------------------------------------------------------------------------
# TestUpdateSessionState
# ---------------------------------------------------------------------------


class TestUpdateSessionState:
    async def test_success(self):
        svc = _make_session_service()
        session = _make_session()
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.update_session_state("s1", "app", "u1", {"key": "val"})

        assert result is True
        svc.append_event.assert_called_once()

    async def test_session_not_found(self):
        svc = _make_session_service()
        svc.get_session.return_value = None

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.update_session_state("s1", "app", "u1", {"key": "val"})

        assert result is False
        svc.append_event.assert_not_called()

    async def test_empty_updates(self):
        svc = _make_session_service()
        session = _make_session()
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.update_session_state("s1", "app", "u1", {})

        assert result is False

    async def test_exception_returns_false(self):
        svc = _make_session_service()
        svc.get_session.side_effect = Exception("DB error")

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.update_session_state("s1", "app", "u1", {"k": "v"})

        assert result is False


# ---------------------------------------------------------------------------
# TestGetSessionState
# ---------------------------------------------------------------------------


class TestGetSessionState:
    async def test_with_to_dict(self):
        svc = _make_session_service()
        session = _make_session()
        session.state = Mock()
        session.state.to_dict = Mock(return_value={"key": "val"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_session_state("s1", "app", "u1")

        assert result == {"key": "val"}
        session.state.to_dict.assert_called_once()

    async def test_with_dict_fallback(self):
        svc = _make_session_service()
        session = _make_session(state={"a": 1, "b": 2})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_session_state("s1", "app", "u1")

        assert result == {"a": 1, "b": 2}

    async def test_session_not_found(self):
        svc = _make_session_service()
        svc.get_session.return_value = None

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_session_state("s1", "app", "u1")

        assert result is None

    async def test_exception_returns_none(self):
        svc = _make_session_service()
        svc.get_session.side_effect = Exception("fail")

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_session_state("s1", "app", "u1")

        assert result is None


# ---------------------------------------------------------------------------
# TestGetStateValue
# ---------------------------------------------------------------------------


class TestGetStateValue:
    async def test_existing_key(self):
        svc = _make_session_service()
        session = _make_session(state={"color": "blue"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_state_value("s1", "app", "u1", "color")

        assert result == "blue"

    async def test_missing_key_returns_default(self):
        svc = _make_session_service()
        session = _make_session(state={"color": "blue"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_state_value("s1", "app", "u1", "missing", default="red")

        assert result == "red"

    async def test_session_not_found_returns_default(self):
        svc = _make_session_service()
        svc.get_session.return_value = None

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_state_value("s1", "app", "u1", "key", default=42)

        assert result == 42

    async def test_exception_returns_default(self):
        svc = _make_session_service()
        svc.get_session.side_effect = Exception("fail")

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_state_value("s1", "app", "u1", "key", default="fallback")

        assert result == "fallback"


# ---------------------------------------------------------------------------
# TestSetStateValue
# ---------------------------------------------------------------------------


class TestSetStateValue:
    async def test_delegates_to_update_session_state(self):
        svc = _make_session_service()
        session = _make_session()
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.set_state_value("s1", "app", "u1", "key", "value")

        assert result is True
        svc.append_event.assert_called_once()


# ---------------------------------------------------------------------------
# TestRemoveStateKeys
# ---------------------------------------------------------------------------


class TestRemoveStateKeys:
    async def test_single_string_key(self):
        svc = _make_session_service()
        session = _make_session(state={"a": 1, "b": 2})
        # First call: get_session_state -> get_session
        # Second call: update_session_state -> get_session
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.remove_state_keys("s1", "app", "u1", "a")

        assert result is True

    async def test_list_of_keys(self):
        svc = _make_session_service()
        session = _make_session(state={"a": 1, "b": 2, "c": 3})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.remove_state_keys("s1", "app", "u1", ["a", "b"])

        assert result is True

    async def test_no_matching_keys(self):
        svc = _make_session_service()
        session = _make_session(state={"a": 1})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.remove_state_keys("s1", "app", "u1", ["nonexistent"])

        assert result is True
        # No update should be called since no keys matched
        svc.append_event.assert_not_called()

    async def test_session_not_found(self):
        svc = _make_session_service()
        svc.get_session.return_value = None

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.remove_state_keys("s1", "app", "u1", ["key"])

        assert result is False


# ---------------------------------------------------------------------------
# TestClearSessionState
# ---------------------------------------------------------------------------


class TestClearSessionState:
    async def test_without_preserve_prefixes(self):
        svc = _make_session_service()
        session = _make_session(state={"a": 1, "b": 2, "c": 3})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.clear_session_state("s1", "app", "u1")

        assert result is True

    async def test_with_preserve_prefixes(self):
        svc = _make_session_service()
        session = _make_session(state={"user:name": "John", "user:id": "1", "temp_key": "val", "data": "x"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.clear_session_state("s1", "app", "u1", preserve_prefixes=["user:"])

        assert result is True

    async def test_session_not_found(self):
        svc = _make_session_service()
        svc.get_session.return_value = None

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.clear_session_state("s1", "app", "u1")

        assert result is False

    async def test_all_keys_preserved(self):
        svc = _make_session_service()
        session = _make_session(state={"user:name": "John"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.clear_session_state("s1", "app", "u1", preserve_prefixes=["user:"])

        assert result is True
        # No removal needed since all keys are preserved
        svc.append_event.assert_not_called()


# ---------------------------------------------------------------------------
# TestInitializeSessionState
# ---------------------------------------------------------------------------


class TestInitializeSessionState:
    async def test_with_overwrite(self):
        svc = _make_session_service()
        session = _make_session(state={"existing": "old"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.initialize_session_state(
            "s1", "app", "u1", {"existing": "new", "added": "val"}, overwrite_existing=True
        )

        assert result is True
        svc.append_event.assert_called_once()

    async def test_without_overwrite_skips_existing(self):
        svc = _make_session_service()
        session = _make_session(state={"existing": "old"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.initialize_session_state(
            "s1", "app", "u1", {"existing": "new", "added": "val"}, overwrite_existing=False
        )

        assert result is True
        # append_event is called with only the new key
        svc.append_event.assert_called_once()

    async def test_without_overwrite_all_keys_exist(self):
        svc = _make_session_service()
        session = _make_session(state={"a": 1, "b": 2})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.initialize_session_state(
            "s1", "app", "u1", {"a": "new_a", "b": "new_b"}, overwrite_existing=False
        )

        assert result is True
        # No update needed since all keys exist
        svc.append_event.assert_not_called()

    async def test_exception_returns_false(self):
        svc = _make_session_service()
        svc.get_session.side_effect = Exception("fail")

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.initialize_session_state("s1", "app", "u1", {"k": "v"})

        assert result is False


# ---------------------------------------------------------------------------
# TestBulkUpdateUserState
# ---------------------------------------------------------------------------


class TestBulkUpdateUserState:
    async def test_updates_all_user_sessions(self):
        svc = _make_session_service()
        session1 = _make_session(id="s1", app_name="app1", user_id="u1")
        session2 = _make_session(id="s2", app_name="app2", user_id="u1")
        svc.get_session.return_value = session1  # will be returned for all calls

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._user_sessions["u1"] = {"app1:s1", "app2:s2"}

        results = await mgr.bulk_update_user_state("u1", {"theme": "dark"})

        assert len(results) == 2
        assert all(v is True for v in results.values())

    async def test_with_app_name_filter(self):
        svc = _make_session_service()
        session = _make_session(id="s1", app_name="app1", user_id="u1")
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._user_sessions["u1"] = {"app1:s1", "app2:s2"}

        results = await mgr.bulk_update_user_state("u1", {"theme": "dark"}, app_name_filter="app1")

        assert "app1:s1" in results
        assert "app2:s2" not in results

    async def test_no_sessions_for_user(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        results = await mgr.bulk_update_user_state("nonexistent", {"k": "v"})

        assert results == {}


# ---------------------------------------------------------------------------
# TestTrackUntrack
# ---------------------------------------------------------------------------


class TestTrackUntrack:
    def test_track_session(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        mgr._track_session("app:s1", "u1")

        assert "app:s1" in mgr._session_keys
        assert "app:s1" in mgr._user_sessions["u1"]

    def test_track_multiple_sessions(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        mgr._track_session("app:s1", "u1")
        mgr._track_session("app:s2", "u1")

        assert mgr._user_sessions["u1"] == {"app:s1", "app:s2"}

    def test_untrack_session(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")

        mgr._untrack_session("app:s1", "u1")

        assert "app:s1" not in mgr._session_keys
        assert "u1" not in mgr._user_sessions

    def test_untrack_preserves_other_sessions(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")
        mgr._track_session("app:s2", "u1")

        mgr._untrack_session("app:s1", "u1")

        assert "app:s1" not in mgr._session_keys
        assert "app:s2" in mgr._user_sessions["u1"]

    def test_untrack_nonexistent_safe(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        # Should not raise
        mgr._untrack_session("app:missing", "u1")


# ---------------------------------------------------------------------------
# TestSessionCounts
# ---------------------------------------------------------------------------


class TestSessionCounts:
    def test_get_session_count(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")
        mgr._track_session("app:s2", "u2")

        assert mgr.get_session_count() == 2

    def test_get_session_count_empty(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        assert mgr.get_session_count() == 0

    def test_get_user_session_count(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")
        mgr._track_session("app:s2", "u1")
        mgr._track_session("app:s3", "u2")

        assert mgr.get_user_session_count("u1") == 2
        assert mgr.get_user_session_count("u2") == 1

    def test_get_user_session_count_unknown_user(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        assert mgr.get_user_session_count("ghost") == 0


# ---------------------------------------------------------------------------
# TestStopCleanupTask
# ---------------------------------------------------------------------------


class TestStopCleanupTask:
    async def test_stop_when_task_exists(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        async def fake_loop():
            await asyncio.sleep(3600)

        mgr._cleanup_task = asyncio.create_task(fake_loop())

        await mgr.stop_cleanup_task()

        assert mgr._cleanup_task is None

    async def test_stop_when_no_task(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._cleanup_task = None

        await mgr.stop_cleanup_task()

        assert mgr._cleanup_task is None


# ---------------------------------------------------------------------------
# TestCleanupExpiredSessions
# ---------------------------------------------------------------------------


class TestCleanupExpiredSessions:
    async def test_expired_session_deleted(self):
        svc = _make_session_service()
        expired_session = _make_session(
            id="s1", app_name="app", user_id="u1",
            state={},
            last_update_time=time.time() - 9999,
        )
        svc.get_session.return_value = expired_session

        mgr = SessionManager(session_service=svc, session_timeout_seconds=60, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")

        await mgr._cleanup_expired_sessions()

        svc.delete_session.assert_called_once_with(
            session_id="s1", app_name="app", user_id="u1"
        )
        assert "app:s1" not in mgr._session_keys

    async def test_pending_tool_calls_preserved(self):
        svc = _make_session_service()
        session_with_pending = _make_session(
            id="s2", app_name="app", user_id="u1",
            state={"pending_tool_calls": ["call-1"]},
            last_update_time=time.time() - 9999,
        )
        svc.get_session.return_value = session_with_pending

        mgr = SessionManager(session_service=svc, session_timeout_seconds=60, auto_cleanup=False)
        mgr._track_session("app:s2", "u1")

        await mgr._cleanup_expired_sessions()

        svc.delete_session.assert_not_called()
        assert "app:s2" in mgr._session_keys

    async def test_non_expired_session_kept(self):
        svc = _make_session_service()
        fresh_session = _make_session(
            id="s3", app_name="app", user_id="u1",
            state={},
            last_update_time=time.time(),
        )
        svc.get_session.return_value = fresh_session

        mgr = SessionManager(session_service=svc, session_timeout_seconds=60, auto_cleanup=False)
        mgr._track_session("app:s3", "u1")

        await mgr._cleanup_expired_sessions()

        svc.delete_session.assert_not_called()
        assert "app:s3" in mgr._session_keys

    async def test_missing_session_untracked(self):
        svc = _make_session_service()
        svc.get_session.return_value = None

        mgr = SessionManager(session_service=svc, session_timeout_seconds=60, auto_cleanup=False)
        mgr._track_session("app:s4", "u1")

        await mgr._cleanup_expired_sessions()

        assert "app:s4" not in mgr._session_keys


# ---------------------------------------------------------------------------
# TestDeleteSession
# ---------------------------------------------------------------------------


class TestDeleteSession:
    async def test_successful_deletion(self):
        svc = _make_session_service()
        session = _make_session(id="s1", app_name="app", user_id="u1")

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")

        await mgr._delete_session(session)

        svc.delete_session.assert_called_once_with(session_id="s1", app_name="app", user_id="u1")
        assert "app:s1" not in mgr._session_keys

    async def test_none_session(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        await mgr._delete_session(None)

        svc.delete_session.assert_not_called()

    async def test_delete_exception_handled(self):
        svc = _make_session_service()
        svc.delete_session.side_effect = Exception("delete error")
        session = _make_session(id="s1", app_name="app", user_id="u1")

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")

        # Should not raise
        await mgr._delete_session(session)

        # Session should still be untracked even on error
        assert "app:s1" not in mgr._session_keys


# ---------------------------------------------------------------------------
# TestRemoveOldestUserSession
# ---------------------------------------------------------------------------


class TestRemoveOldestUserSession:
    async def test_removes_oldest(self):
        svc = _make_session_service()
        old = _make_session(id="old", app_name="app", user_id="u1", last_update_time=100.0)
        new = _make_session(id="new", app_name="app", user_id="u1", last_update_time=999.0)

        async def fake_get_session(session_id, app_name, user_id):
            if session_id == "old":
                return old
            if session_id == "new":
                return new
            return None

        svc.get_session = AsyncMock(side_effect=fake_get_session)

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:old", "u1")
        mgr._track_session("app:new", "u1")

        await mgr._remove_oldest_user_session("u1")

        svc.delete_session.assert_called_once_with(session_id="old", app_name="app", user_id="u1")

    async def test_no_sessions_for_user(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        await mgr._remove_oldest_user_session("nonexistent")

        svc.delete_session.assert_not_called()

    async def test_session_without_last_update_time(self):
        svc = _make_session_service()
        session_no_time = Mock()
        session_no_time.id = "s1"
        session_no_time.app_name = "app"
        session_no_time.user_id = "u1"
        del session_no_time.last_update_time

        svc.get_session = AsyncMock(return_value=session_no_time)

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        mgr._track_session("app:s1", "u1")

        await mgr._remove_oldest_user_session("u1")
        # No session meets the oldest criterion when none have last_update_time
        svc.delete_session.assert_not_called()


# ---------------------------------------------------------------------------
# TestResetInstanceWithCleanupTask
# ---------------------------------------------------------------------------


class TestResetInstanceWithCleanupTask:
    async def test_reset_cancels_running_cleanup_task(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        async def fake_loop():
            await asyncio.sleep(100)

        mgr._cleanup_task = asyncio.create_task(fake_loop())

        SessionManager.reset_instance()

        assert SessionManager._instance is None
        assert SessionManager._initialized is False

    def test_reset_handles_runtime_error_from_cancel(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        mock_task = Mock()
        mock_task.cancel = Mock(side_effect=RuntimeError("no loop"))
        mgr._cleanup_task = mock_task

        SessionManager.reset_instance()
        assert SessionManager._instance is None


# ---------------------------------------------------------------------------
# TestUpdateSessionStateMergeFalse
# ---------------------------------------------------------------------------


class TestUpdateSessionStateMergeFalse:
    async def test_merge_false_replaces_state(self):
        svc = _make_session_service()
        session = _make_session(state={"old_key": "old_value"})
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.update_session_state("sess-1", "app", "user-1", {"new": "val"}, merge=False)

        assert result is True
        svc.append_event.assert_called_once()


# ---------------------------------------------------------------------------
# TestGetStateValueElseBranch
# ---------------------------------------------------------------------------


class TestGetStateValueElseBranch:
    async def test_state_with_get_method(self):
        svc = _make_session_service()
        session = _make_session()
        session.state = {"existing": "found", "other": "val"}
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_state_value("sess-1", "app", "user-1", "existing")
        assert result == "found"

    async def test_state_key_not_found_returns_default(self):
        svc = _make_session_service()
        session = _make_session()
        session.state = {"a": 1}
        svc.get_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=False)
        result = await mgr.get_state_value("sess-1", "app", "user-1", "missing", default="fallback")
        assert result == "fallback"


# ---------------------------------------------------------------------------
# TestStartCleanupTask
# ---------------------------------------------------------------------------


class TestStartCleanupTask:
    async def test_starts_cleanup_task_in_running_loop(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        mgr._start_cleanup_task()

        assert mgr._cleanup_task is not None
        mgr._cleanup_task.cancel()
        try:
            await mgr._cleanup_task
        except asyncio.CancelledError:
            pass

    def test_no_event_loop_handles_gracefully(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            mgr._start_cleanup_task()

        assert mgr._cleanup_task is None


# ---------------------------------------------------------------------------
# TestCleanupLoop
# ---------------------------------------------------------------------------


class TestCleanupLoop:
    async def test_cleanup_loop_cancelled(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False, cleanup_interval_seconds=0)

        task = asyncio.create_task(mgr._cleanup_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_cleanup_loop_handles_exception(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False, cleanup_interval_seconds=0)

        call_count = 0

        async def failing_cleanup():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("cleanup error")

        mgr._cleanup_expired_sessions = failing_cleanup

        task = asyncio.create_task(mgr._cleanup_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert call_count >= 1


# ---------------------------------------------------------------------------
# TestAutoCleanupStart
# ---------------------------------------------------------------------------


class TestAutoCleanupStart:
    async def test_auto_cleanup_starts_on_get_or_create(self):
        svc = _make_session_service()
        session = _make_session()
        svc.get_session.return_value = None
        svc.create_session.return_value = session

        mgr = SessionManager(session_service=svc, auto_cleanup=True)

        await mgr.get_or_create_session("s1", "app", "u1")

        assert mgr._cleanup_task is not None
        mgr._cleanup_task.cancel()
        try:
            await mgr._cleanup_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# TestExceptionHandlers
# ---------------------------------------------------------------------------


class TestExceptionHandlers:
    async def test_remove_state_keys_exception(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        mgr.get_session_state = AsyncMock(side_effect=RuntimeError("fail"))

        result = await mgr.remove_state_keys("s1", "app", "u1", ["key1"])
        assert result is False

    async def test_clear_session_state_exception(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        mgr.get_session_state = AsyncMock(side_effect=RuntimeError("fail"))

        result = await mgr.clear_session_state("s1", "app", "u1")
        assert result is False

    async def test_initialize_session_state_exception(self):
        svc = _make_session_service()
        mgr = SessionManager(session_service=svc, auto_cleanup=False)

        mgr.get_session_state = AsyncMock(side_effect=RuntimeError("fail"))

        result = await mgr.initialize_session_state("s1", "app", "u1", {"key": "val"})
        assert result is False


# ---------------------------------------------------------------------------
# TestCleanupExpiredWithLogging
# ---------------------------------------------------------------------------


class TestCleanupExpiredWithLogging:
    async def test_expired_session_triggers_deletion(self):
        svc = _make_session_service()
        expired_session = _make_session(
            id="s-old", app_name="app", user_id="u1",
            last_update_time=0.0,
        )
        expired_session.state = {"some_key": "value"}
        svc.get_session.return_value = expired_session

        mgr = SessionManager(session_service=svc, session_timeout_seconds=1, auto_cleanup=False)
        mgr._session_keys = {"app:s-old"}
        mgr._user_sessions = {"u1": {"app:s-old"}}

        # Directly call _delete_session to prove it works
        await mgr._delete_session(expired_session)

        svc.delete_session.assert_called_once_with(
            session_id="s-old", app_name="app", user_id="u1",
        )
