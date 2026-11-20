# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the in-process cancel manager."""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.cancel import SessionKey
from trpc_agent_sdk.cancel import SteeringStatus
from trpc_agent_sdk.cancel._local_manager import _LocalManager
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.types import Blob
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _text(text: str) -> Content:
    return Content(role="user", parts=[Part(text=text)])


@pytest.fixture
def manager():
    return _LocalManager()


class TestSessionKey:

    def test_frozen_and_hashable(self):
        key = SessionKey("app", "u1", "s1")
        with pytest.raises(AttributeError):
            key.app_name = "other"
        assert {key: 1}[SessionKey("app", "u1", "s1")] == 1

    def test_inequality(self):
        assert SessionKey("app", "u1", "s1") != SessionKey("app", "u1", "s2")


class TestLifecycle:

    def test_register_and_finish(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        assert manager.get_cancel_event(key) is not None
        manager.unregister_run(key)
        assert manager.get_cancel_event(key) is None

    def test_finish_is_idempotent(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        manager.unregister_run(key)
        manager.unregister_run(key)

    def test_duplicate_register_raises(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        with pytest.raises(RuntimeError):
            manager.register_run(key)
        manager.unregister_run(key)
        manager.register_run(key)
        manager.unregister_run(key)


class TestCancel:

    async def test_cancel_without_run_returns_false(self, manager):
        assert await manager.cancel_run(SessionKey("app", "u1", "missing")) is False

    async def test_cancel_sets_event_and_raises_at_checkpoint(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        event = manager.get_cancel_event(key)

        task = asyncio.create_task(manager.cancel_run(key))
        await asyncio.sleep(0)
        assert event.is_set()
        with pytest.raises(RunCancelledException):
            manager.raise_if_cancelled(key)

        manager.unregister_run(key)
        assert await task is True

    async def test_cancel_waits_for_cleanup(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)

        task = asyncio.create_task(manager.cancel_run(key))
        await asyncio.sleep(0.01)
        assert not task.done()

        manager.unregister_run(key)
        assert await task is True

    async def test_cancel_after_finish_returns_false(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        manager.unregister_run(key)
        assert await manager.cancel_run(key) is False


class TestSteering:

    def test_steering_fifo(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        try:
            assert manager.send_steering(key, _text("first")) is SteeringStatus.ACCEPTED
            assert manager.send_steering(key, _text("second")) is SteeringStatus.ACCEPTED
            assert [item.parts[0].text for item in manager.drain_steering(key)] == ["first", "second"]
            assert manager.drain_steering(key) == []
        finally:
            manager.unregister_run(key)

    def test_steering_requires_active_run(self, manager):
        assert manager.send_steering(SessionKey("app", "u1", "missing"), _text("hi")) is SteeringStatus.NO_ACTIVE_RUN

    def test_steering_accepts_multimodal(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        try:
            image = b"\x89PNG\r\n\x1a\n\x00\xff"
            content = Content(
                role="user",
                parts=[Part(text="look"), Part(inline_data=Blob(mime_type="image/png", data=image))],
            )
            assert manager.send_steering(key, content) is SteeringStatus.ACCEPTED
            [received] = manager.drain_steering(key)
            assert received.parts[0].text == "look"
            assert received.parts[1].inline_data.data == image
        finally:
            manager.unregister_run(key)

    def test_steering_rejects_empty_content(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        try:
            assert manager.send_steering(key, Content(role="user")) is SteeringStatus.UNSUPPORTED_CONTENT
            assert manager.send_steering(key, Content(role="user", parts=[])) is SteeringStatus.UNSUPPORTED_CONTENT
        finally:
            manager.unregister_run(key)

    def test_steering_is_dropped_after_finish(self, manager):
        key = SessionKey("app", "u1", "s1")
        manager.register_run(key)
        manager.unregister_run(key)
        assert manager.drain_steering(key) == []
        assert manager.send_steering(key, _text("late")) is SteeringStatus.NO_ACTIVE_RUN
