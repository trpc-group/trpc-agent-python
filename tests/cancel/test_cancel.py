# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.cancel._cancel.

Covers:
- SessionKey (frozen dataclass, equality, hashing)
- _RunCancellationManager (register, cancel, is_cancelled, cleanup, get_active_sessions, get_cancel_event)
- Module-level convenience functions (register_run, cancel_run, is_run_cancelled, cleanup_run, raise_if_cancelled, get_cancel_event)
"""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.cancel._cancel import (
    SessionKey,
    _RunCancellationManager,
    _manager,
    cancel_run,
    cleanup_run,
    get_cancel_event,
    is_run_cancelled,
    raise_if_cancelled,
    register_run,
)
from trpc_agent_sdk.exceptions import RunCancelledException


@pytest.fixture(autouse=True)
async def _reset_manager():
    """Reset the singleton manager state before each test."""
    async with _manager._lock:
        _manager._cancelled.clear()
        _manager._cleanup_events.clear()
    yield
    async with _manager._lock:
        _manager._cancelled.clear()
        _manager._cleanup_events.clear()


# ---------------------------------------------------------------------------
# SessionKey
# ---------------------------------------------------------------------------
class TestSessionKey:
    """Tests for SessionKey frozen dataclass."""

    def test_creation(self):
        key = SessionKey(app_name="app", user_id="u1", session_id="s1")
        assert key.app_name == "app"
        assert key.user_id == "u1"
        assert key.session_id == "s1"

    def test_frozen(self):
        key = SessionKey(app_name="app", user_id="u1", session_id="s1")
        with pytest.raises(AttributeError):
            key.app_name = "other"

    def test_equality(self):
        k1 = SessionKey("app", "u1", "s1")
        k2 = SessionKey("app", "u1", "s1")
        assert k1 == k2

    def test_inequality(self):
        k1 = SessionKey("app", "u1", "s1")
        k2 = SessionKey("app", "u1", "s2")
        assert k1 != k2

    def test_hashable_dict_key(self):
        k1 = SessionKey("app", "u1", "s1")
        k2 = SessionKey("app", "u1", "s1")
        d = {k1: "value"}
        assert d[k2] == "value"

    def test_hash_differs_for_different_keys(self):
        k1 = SessionKey("app", "u1", "s1")
        k2 = SessionKey("app", "u1", "s2")
        assert hash(k1) != hash(k2)


# ---------------------------------------------------------------------------
# _RunCancellationManager
# ---------------------------------------------------------------------------
class TestRunCancellationManager:
    """Tests for _RunCancellationManager."""

    async def test_register_run_returns_session_key(self):
        key = await _manager.register_run("app", "u1", "s1")
        assert isinstance(key, SessionKey)
        assert key == SessionKey("app", "u1", "s1")

    async def test_register_run_creates_unset_cancel_event(self):
        key = await _manager.register_run("app", "u1", "s1")
        assert key in _manager._cancelled
        assert not _manager._cancelled[key].is_set()

    async def test_cancel_run_sets_event(self):
        await _manager.register_run("app", "u1", "s1")
        cleanup_event = await _manager.cancel_run("app", "u1", "s1")
        assert cleanup_event is not None
        key = SessionKey("app", "u1", "s1")
        assert _manager._cancelled[key].is_set()

    async def test_cancel_run_returns_cleanup_event(self):
        await _manager.register_run("app", "u1", "s1")
        cleanup_event = await _manager.cancel_run("app", "u1", "s1")
        assert isinstance(cleanup_event, asyncio.Event)
        assert not cleanup_event.is_set()

    async def test_cancel_run_unregistered_returns_none(self):
        result = await _manager.cancel_run("app", "u1", "nonexistent")
        assert result is None

    async def test_is_cancelled_true_after_cancel(self):
        await _manager.register_run("app", "u1", "s1")
        await _manager.cancel_run("app", "u1", "s1")
        key = SessionKey("app", "u1", "s1")
        assert await _manager.is_cancelled(key) is True

    async def test_is_cancelled_false_before_cancel(self):
        key = await _manager.register_run("app", "u1", "s1")
        assert await _manager.is_cancelled(key) is False

    async def test_is_cancelled_false_for_unknown_key(self):
        key = SessionKey("app", "u1", "unknown")
        assert await _manager.is_cancelled(key) is False

    async def test_cleanup_run_removes_tracking(self):
        key = await _manager.register_run("app", "u1", "s1")
        await _manager.cleanup_run("app", "u1", "s1")
        assert key not in _manager._cancelled

    async def test_cleanup_run_signals_cleanup_event(self):
        await _manager.register_run("app", "u1", "s1")
        cleanup_event = await _manager.cancel_run("app", "u1", "s1")
        await _manager.cleanup_run("app", "u1", "s1")
        assert cleanup_event.is_set()

    async def test_cleanup_run_removes_cleanup_event(self):
        await _manager.register_run("app", "u1", "s1")
        await _manager.cancel_run("app", "u1", "s1")
        key = SessionKey("app", "u1", "s1")
        await _manager.cleanup_run("app", "u1", "s1")
        assert key not in _manager._cleanup_events

    async def test_cleanup_run_noop_for_unknown_session(self):
        await _manager.cleanup_run("app", "u1", "nonexistent")

    async def test_get_active_sessions_empty(self):
        result = _manager.get_active_sessions()
        assert result == {}

    async def test_get_active_sessions_with_registered_run(self):
        await _manager.register_run("app", "u1", "s1")
        result = _manager.get_active_sessions()
        assert ("app", "u1", "s1") in result
        assert result[("app", "u1", "s1")] is False

    async def test_get_active_sessions_shows_cancelled_status(self):
        await _manager.register_run("app", "u1", "s1")
        await _manager.cancel_run("app", "u1", "s1")
        result = _manager.get_active_sessions()
        assert result[("app", "u1", "s1")] is True

    async def test_get_active_sessions_multiple(self):
        await _manager.register_run("app", "u1", "s1")
        await _manager.register_run("app", "u2", "s2")
        await _manager.cancel_run("app", "u1", "s1")
        result = _manager.get_active_sessions()
        assert len(result) == 2
        assert result[("app", "u1", "s1")] is True
        assert result[("app", "u2", "s2")] is False

    async def test_get_cancel_event_returns_event(self):
        key = await _manager.register_run("app", "u1", "s1")
        event = await _manager.get_cancel_event(key)
        assert isinstance(event, asyncio.Event)
        assert not event.is_set()

    async def test_get_cancel_event_returns_none_for_unknown(self):
        key = SessionKey("app", "u1", "unknown")
        event = await _manager.get_cancel_event(key)
        assert event is None

    async def test_get_cancel_event_reflects_cancellation(self):
        key = await _manager.register_run("app", "u1", "s1")
        await _manager.cancel_run("app", "u1", "s1")
        event = await _manager.get_cancel_event(key)
        assert event is not None
        assert event.is_set()

    async def test_re_register_overwrites_previous(self):
        key = await _manager.register_run("app", "u1", "s1")
        await _manager.cancel_run("app", "u1", "s1")
        assert await _manager.is_cancelled(key) is True

        key2 = await _manager.register_run("app", "u1", "s1")
        assert key == key2
        assert await _manager.is_cancelled(key2) is False


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------
class TestModuleLevelFunctions:
    """Tests for module-level convenience functions that delegate to _manager."""

    async def test_register_run(self):
        key = await register_run("app", "u1", "s1")
        assert isinstance(key, SessionKey)
        assert key == SessionKey("app", "u1", "s1")

    async def test_cancel_run_returns_cleanup_event(self):
        await register_run("app", "u1", "s1")
        cleanup_event = await cancel_run("app", "u1", "s1")
        assert isinstance(cleanup_event, asyncio.Event)

    async def test_cancel_run_returns_none_if_not_registered(self):
        result = await cancel_run("app", "u1", "nonexistent")
        assert result is None

    async def test_is_run_cancelled(self):
        key = await register_run("app", "u1", "s1")
        assert await is_run_cancelled(key) is False
        await cancel_run("app", "u1", "s1")
        assert await is_run_cancelled(key) is True

    async def test_cleanup_run(self):
        key = await register_run("app", "u1", "s1")
        await cleanup_run("app", "u1", "s1")
        assert await is_run_cancelled(key) is False

    async def test_get_cancel_event(self):
        key = await register_run("app", "u1", "s1")
        event = await get_cancel_event(key)
        assert isinstance(event, asyncio.Event)
        assert not event.is_set()

    async def test_get_cancel_event_none_for_unknown(self):
        key = SessionKey("app", "u1", "unknown")
        event = await get_cancel_event(key)
        assert event is None


class TestRaiseIfCancelled:
    """Tests for raise_if_cancelled."""

    async def test_no_exception_when_not_cancelled(self):
        key = await register_run("app", "u1", "s1")
        await raise_if_cancelled(key)

    async def test_raises_when_cancelled(self):
        key = await register_run("app", "u1", "s1")
        await cancel_run("app", "u1", "s1")
        with pytest.raises(RunCancelledException, match="s1"):
            await raise_if_cancelled(key)

    async def test_no_exception_for_unknown_session(self):
        key = SessionKey("app", "u1", "unknown")
        await raise_if_cancelled(key)

    async def test_exception_message_contains_session_id(self):
        key = await register_run("app", "u1", "my-session")
        await cancel_run("app", "u1", "my-session")
        with pytest.raises(RunCancelledException) as exc_info:
            await raise_if_cancelled(key)
        assert "my-session" in str(exc_info.value)


class TestCancelRunAsyncWaitFlow:
    """Tests for the full cancel -> wait for cleanup flow."""

    async def test_cancel_then_cleanup_signals_waiter(self):
        await register_run("app", "u1", "s1")
        cleanup_event = await cancel_run("app", "u1", "s1")
        assert not cleanup_event.is_set()

        await cleanup_run("app", "u1", "s1")
        assert cleanup_event.is_set()

    async def test_concurrent_cancel_and_cleanup(self):
        """Simulate real-world: one task cancels, another finishes and cleans up."""
        key = await register_run("app", "u1", "s1")

        async def canceller():
            cleanup_event = await cancel_run("app", "u1", "s1")
            await asyncio.wait_for(cleanup_event.wait(), timeout=2.0)
            return True

        async def worker():
            await asyncio.sleep(0.05)
            if await is_run_cancelled(key):
                await cleanup_run("app", "u1", "s1")

        cancel_task = asyncio.create_task(canceller())
        worker_task = asyncio.create_task(worker())

        await asyncio.gather(cancel_task, worker_task)
        assert cancel_task.result() is True
