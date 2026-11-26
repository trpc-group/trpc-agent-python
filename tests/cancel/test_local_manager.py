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
from trpc_agent_sdk.cancel import Status
from trpc_agent_sdk.cancel._local_manager import _LocalManager
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

    async def test_register_and_finish(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        assert await manager.get_cancel_event(key) is not None
        await manager.unregister_run(key)
        assert await manager.get_cancel_event(key) is None

    async def test_finish_is_idempotent(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        await manager.unregister_run(key)
        await manager.unregister_run(key)

    async def test_duplicate_register_raises(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        with pytest.raises(RuntimeError):
            await manager.register_run(key)
        await manager.unregister_run(key)
        await manager.register_run(key)
        await manager.unregister_run(key)


class TestCancel:

    async def test_cancel_without_run_returns_false(self, manager):
        status = await manager.cancel_run(SessionKey("app", "u1", "missing"))
        assert status.ok is False
        assert status.error_msg == "no active run"

    async def test_cancel_sets_event_and_check_returns_true(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        event = await manager.get_cancel_event(key)

        task = asyncio.create_task(manager.cancel_run(key))
        await asyncio.sleep(0)
        assert event.is_set()
        assert await manager.check_cancel(key) is True

        await manager.unregister_run(key)
        assert (await task).ok is True

    async def test_cancel_waits_for_cleanup(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)

        task = asyncio.create_task(manager.cancel_run(key))
        await asyncio.sleep(0.01)
        assert not task.done()

        await manager.unregister_run(key)
        assert (await task).ok is True

    async def test_cancel_after_finish_returns_false(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        await manager.unregister_run(key)
        status = await manager.cancel_run(key)
        assert status.ok is False
        assert status.error_msg == "no active run"

    async def test_cancel_timeout_returns_false(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        try:
            status = await manager.cancel_run(key, timeout=0.01)
            assert status.ok is False
            assert "timed out" in status.error_msg
        finally:
            await manager.unregister_run(key)


class TestSteering:

    async def test_steering_fifo(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        try:
            assert (await manager.send_steering(key, _text("first"))).ok
            assert (await manager.send_steering(key, _text("second"))).ok
            assert [item.parts[0].text for item in await manager.drain_steering(key)] == ["first", "second"]
            assert await manager.drain_steering(key) == []
        finally:
            await manager.unregister_run(key)

    async def test_steering_requires_active_run(self, manager):
        status = await manager.send_steering(SessionKey("app", "u1", "missing"), _text("hi"))
        assert status.ok is False
        assert status.error_msg == "no active run"

    async def test_steering_accepts_multimodal(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        try:
            image = b"\x89PNG\r\n\x1a\n\x00\xff"
            content = Content(
                role="user",
                parts=[Part(text="look"), Part(inline_data=Blob(mime_type="image/png", data=image))],
            )
            assert (await manager.send_steering(key, content)).ok
            [received] = await manager.drain_steering(key)
            assert received.parts[0].text == "look"
            assert received.parts[1].inline_data.data == image
        finally:
            await manager.unregister_run(key)

    async def test_steering_rejects_empty_content(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        try:
            first = await manager.send_steering(key, Content(role="user"))
            second = await manager.send_steering(key, Content(role="user", parts=[]))
            assert first.ok is False
            assert first.error_msg == "steering content is empty"
            assert second.ok is False
            assert second.error_msg == "steering content is empty"
        finally:
            await manager.unregister_run(key)

    async def test_steering_is_dropped_after_finish(self, manager):
        key = SessionKey("app", "u1", "s1")
        await manager.register_run(key)
        await manager.unregister_run(key)
        assert await manager.drain_steering(key) == []
        status = await manager.send_steering(key, _text("late"))
        assert status.ok is False
        assert status.error_msg == "no active run"
