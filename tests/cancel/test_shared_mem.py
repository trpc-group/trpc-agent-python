# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the shared-memory control plane."""

from __future__ import annotations

import asyncio
import math
import mmap
import multiprocessing
import os
import time

import pytest

from trpc_agent_sdk import cancel
from trpc_agent_sdk.cancel import SessionKey
from trpc_agent_sdk.cancel import SharedMemoryConfig
from trpc_agent_sdk.cancel import SteeringStatus
from trpc_agent_sdk.cancel import _public
from trpc_agent_sdk.cancel import _shared_mem
from trpc_agent_sdk.cancel._shared_manager import _SharedManager
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.types import Blob
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

_CONFIG = SharedMemoryConfig(
    max_active_runs=4,
    steering_queue_size=2,
    max_steering_bytes=512,
    cancel_poll_interval=0.01,
)


def _text(text: str) -> Content:
    return Content(role="user", parts=[Part(text=text)])


def _open_peer(config: SharedMemoryConfig) -> _shared_mem._SharedMem:
    """Open a second mapping of the segment without using attach()."""
    fd = os.open(_shared_mem.SEGMENT_PATH, os.O_RDWR)
    mm = mmap.mmap(fd, 0)
    return _shared_mem._SharedMem(
        fd=fd,
        mm=mm,
        view=memoryview(mm),
        config=config,
        path=_shared_mem.SEGMENT_PATH,
        owner_pid=-1,
    )


@pytest.fixture
def shared_mem():
    cancel.destroy_shared_mem()
    mem = _shared_mem.init_shared_mem(_CONFIG)
    yield mem
    _shared_mem.destroy_shared_mem(mem)


@pytest.fixture
def public_shared():
    cancel.destroy_shared_mem()
    cancel.init_shared_mem(_CONFIG)
    yield _public._manager
    cancel.destroy_shared_mem()


def _child_attach_and_wait(attached, seen, key_tuple) -> None:
    mem = _open_peer(_CONFIG)
    slot = mem.allocate(SessionKey(*key_tuple))
    attached.set()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if mem.is_cancelled(slot):
            seen.set()
            break
        time.sleep(0.01)
    mem.release(slot)


class TestInit:

    def test_creates_segment_and_manager(self, public_shared):
        assert os.path.exists(_shared_mem.SEGMENT_PATH)
        assert public_shared._mem.config == _CONFIG
        assert public_shared._mem.owner_pid == os.getpid()

    def test_repeated_init_same_config_is_noop(self, public_shared):
        cancel.init_shared_mem(_CONFIG)
        assert _public._manager is public_shared

    def test_repeated_init_different_config_raises(self, public_shared):
        with pytest.raises(ValueError):
            cancel.init_shared_mem(SharedMemoryConfig(max_active_runs=8))

    def test_destroy_removes_segment(self):
        cancel.destroy_shared_mem()
        cancel.init_shared_mem(_CONFIG)
        assert os.path.exists(_shared_mem.SEGMENT_PATH)
        cancel.destroy_shared_mem()
        assert not os.path.exists(_shared_mem.SEGMENT_PATH)

    def test_restart_recreates_segment(self):
        cancel.destroy_shared_mem()
        cancel.init_shared_mem(_CONFIG)
        first = _public._manager._mem
        first.allocate(SessionKey("app", "u", "s"))
        cancel.destroy_shared_mem()

        new_config = SharedMemoryConfig(max_active_runs=8, steering_queue_size=2, max_steering_bytes=512)
        cancel.init_shared_mem(new_config)
        second = _public._manager._mem
        assert second is not first
        assert second.config == new_config
        cancel.destroy_shared_mem()

    def test_header_metadata(self, shared_mem):
        magic, owner_pid, created_at_ms = _shared_mem._HEADER_STRUCT.unpack_from(shared_mem._view, 0)
        assert magic == _shared_mem._MAGIC
        assert owner_pid == os.getpid()
        assert created_at_ms > 0

    def test_default_cancel_poll_interval(self):
        assert SharedMemoryConfig().cancel_poll_interval == 0.1

    @pytest.mark.parametrize("value", [0.0, -0.1, math.nan, math.inf, 0.0001])
    def test_invalid_cancel_poll_interval(self, value):
        with pytest.raises(ValueError):
            SharedMemoryConfig(cancel_poll_interval=value)


class TestTable:

    def test_allocate_and_duplicate(self, shared_mem):
        key = SessionKey("app", "u", "s")
        slot = shared_mem.allocate(key)
        with pytest.raises(RuntimeError):
            shared_mem.allocate(key)
        shared_mem.release(slot)

    def test_cancel_from_second_mem(self, shared_mem):
        other = _open_peer(shared_mem.config)
        key = SessionKey("app", "u", "s")
        try:
            slot = shared_mem.allocate(key)
            found = other.find(key)
            assert found is not None
            assert other.publish_cancel(found) is True
            assert shared_mem.is_cancelled(slot) is True
            assert other.find(SessionKey("app", "u", "missing")) is None
            shared_mem.release(slot)
        finally:
            other.close()

    def test_steering_fifo_and_queue_full(self, shared_mem):
        key = SessionKey("app", "u", "s")
        slot = shared_mem.allocate(key)
        try:
            assert shared_mem.enqueue(slot, _text("first")) is SteeringStatus.ACCEPTED
            assert shared_mem.enqueue(slot, _text("second")) is SteeringStatus.ACCEPTED
            assert shared_mem.enqueue(slot, _text("third")) is SteeringStatus.QUEUE_FULL
            assert [item.parts[0].text for item in shared_mem.drain(slot)] == ["first", "second"]
            assert shared_mem.drain(slot) == []
            assert shared_mem.enqueue(slot, _text("third")) is SteeringStatus.ACCEPTED
        finally:
            shared_mem.release(slot)

    def test_steering_multimodal_round_trip(self, shared_mem):
        key = SessionKey("app", "u", "s")
        slot = shared_mem.allocate(key)
        try:
            image = b"\x89PNG\r\n\x1a\n\x00\xff"
            content = Content(
                role="user",
                parts=[Part(text="look"), Part(inline_data=Blob(mime_type="image/png", data=image))],
            )
            assert shared_mem.enqueue(slot, content) is SteeringStatus.ACCEPTED
            [received] = shared_mem.drain(slot)
            assert received.parts[0].text == "look"
            assert received.parts[1].inline_data.mime_type == "image/png"
            assert received.parts[1].inline_data.data == image
        finally:
            shared_mem.release(slot)

    def test_steering_message_too_large(self, shared_mem):
        key = SessionKey("app", "u", "s")
        slot = shared_mem.allocate(key)
        try:
            assert shared_mem.enqueue(slot, _text("x" * 1000)) is SteeringStatus.MESSAGE_TOO_LARGE
        finally:
            shared_mem.release(slot)

    def test_release_clears_entry(self, shared_mem):
        key = SessionKey("app", "u", "s")
        slot = shared_mem.allocate(key)
        shared_mem.release(slot)
        assert shared_mem.find(key) is None
        assert shared_mem.is_cancelled(slot) is True

    def test_stale_release_does_not_free_new_generation(self, shared_mem, monkeypatch):
        key = SessionKey("app", "u", "s")
        first = shared_mem.allocate(key)
        monkeypatch.setattr(_shared_mem, "pid_is_alive", lambda _pid: False)
        second = shared_mem.allocate(key)
        monkeypatch.undo()

        shared_mem.release(first)
        found = shared_mem.find(key)
        assert found is not None
        assert found.generation == second.generation
        shared_mem.release(second)

    def test_full_table(self):
        cancel.destroy_shared_mem()
        mem = _shared_mem.init_shared_mem(
            SharedMemoryConfig(max_active_runs=1, steering_queue_size=1, max_steering_bytes=128))
        try:
            slot = mem.allocate(SessionKey("app", "u", "s1"))
            with pytest.raises(RuntimeError):
                mem.allocate(SessionKey("app", "u", "s2"))
            mem.release(slot)
        finally:
            _shared_mem.destroy_shared_mem(mem)

    def test_dead_owner_entry_is_reclaimed(self):
        cancel.destroy_shared_mem()
        mem = _shared_mem.init_shared_mem(
            SharedMemoryConfig(max_active_runs=1, steering_queue_size=1, max_steering_bytes=128))
        try:
            first = mem.allocate(SessionKey("app", "u", "s1"))
            with pytest.MonkeyPatch.context() as monkeypatch:
                monkeypatch.setattr(_shared_mem, "pid_is_alive", lambda _pid: False)
                second = mem.allocate(SessionKey("app", "u", "s2"))
            assert second.entry_index == first.entry_index
            assert second.generation > first.generation
            mem.release(second)
        finally:
            _shared_mem.destroy_shared_mem(mem)


class TestManagerShared:

    async def test_cancel_between_managers(self, shared_mem):
        other_mem = _open_peer(shared_mem.config)
        owner = _SharedManager(shared_mem)
        peer = _SharedManager(other_mem)
        key = SessionKey("app", "u", "s")
        try:
            owner.register_run(key)
            assert await peer.cancel_run(key) is True
            with pytest.raises(RunCancelledException):
                owner.raise_if_cancelled(key)
            owner.unregister_run(key)
        finally:
            other_mem.close()

    async def test_watcher_uses_configured_interval(self, shared_mem):
        assert shared_mem.config.cancel_poll_interval == 0.01
        other_mem = _open_peer(shared_mem.config)
        owner = _SharedManager(shared_mem)
        peer = _SharedManager(other_mem)
        key = SessionKey("app", "u", "s")
        try:
            owner.register_run(key)
            event = owner.get_cancel_event(key)
            assert event is not None
            assert await peer.cancel_run(key) is True
            assert await asyncio.wait_for(event.wait(), timeout=0.5) is True
        finally:
            owner.unregister_run(key)
            other_mem.close()

    async def test_cancel_returns_before_cleanup(self, shared_mem):
        owner = _SharedManager(shared_mem)
        key = SessionKey("app", "u", "s")
        owner.register_run(key)
        try:
            assert await asyncio.wait_for(owner.cancel_run(key, timeout=10.0), timeout=0.5) is True
            with pytest.raises(RunCancelledException):
                owner.raise_if_cancelled(key)
        finally:
            owner.unregister_run(key)

    async def test_raise_if_cancelled_sees_shared_publish(self, shared_mem):
        other_mem = _open_peer(shared_mem.config)
        owner = _SharedManager(shared_mem)
        peer = _SharedManager(other_mem)
        key = SessionKey("app", "u", "s")
        try:
            owner.register_run(key)
            event = owner.get_cancel_event(key)
            assert event is not None
            assert await peer.cancel_run(key) is True
            with pytest.raises(RunCancelledException):
                owner.raise_if_cancelled(key)
            assert event.is_set()
            owner.unregister_run(key)
        finally:
            other_mem.close()

    async def test_cancel_event_wakes_from_peer_cancel(self, shared_mem):
        other_mem = _open_peer(shared_mem.config)
        owner = _SharedManager(shared_mem)
        peer = _SharedManager(other_mem)
        key = SessionKey("app", "u", "s")
        try:
            owner.register_run(key)
            event = owner.get_cancel_event(key)
            assert event is not None
            waiter = asyncio.create_task(event.wait())
            assert await peer.cancel_run(key) is True
            assert await asyncio.wait_for(waiter, timeout=1.0) is True
            assert event.is_set()
        finally:
            owner.unregister_run(key)
            other_mem.close()

    async def test_get_cancel_event_starts_single_watcher(self, shared_mem):
        owner = _SharedManager(shared_mem)
        key = SessionKey("app", "u", "s")
        owner.register_run(key)
        run = owner._get_run(key)
        assert run is not None
        task = None
        try:
            assert run.watch_task is None
            first = owner.get_cancel_event(key)
            task = run.watch_task
            assert first is not None
            assert task is not None

            second = owner.get_cancel_event(key)
            assert second is first
            assert run.watch_task is task
        finally:
            owner.unregister_run(key)

        await asyncio.sleep(0.1)
        assert task is not None
        assert task.done()

    async def test_get_cancel_event_after_peer_cancel_skips_watcher(self, shared_mem):
        other_mem = _open_peer(shared_mem.config)
        owner = _SharedManager(shared_mem)
        peer = _SharedManager(other_mem)
        key = SessionKey("app", "u", "s")
        try:
            owner.register_run(key)
            assert await peer.cancel_run(key) is True
            event = owner.get_cancel_event(key)
            run = owner._get_run(key)
            assert event is not None
            assert event.is_set()
            assert run is not None
            assert run.watch_task is None
        finally:
            owner.unregister_run(key)
            other_mem.close()

    async def test_close_cancels_watcher(self, shared_mem):
        owner = _SharedManager(shared_mem)
        key = SessionKey("app", "u", "s")
        owner.register_run(key)
        event = owner.get_cancel_event(key)
        assert event is not None
        run = owner._get_run(key)
        assert run is not None
        task = run.watch_task
        assert task is not None

        owner.close()
        await asyncio.sleep(0.1)
        assert task.done()

    def test_steering_between_managers(self, shared_mem):
        other_mem = _open_peer(shared_mem.config)
        owner = _SharedManager(shared_mem)
        peer = _SharedManager(other_mem)
        key = SessionKey("app", "u", "s")
        try:
            owner.register_run(key)
            assert peer.send_steering(key, _text("remote")) is SteeringStatus.ACCEPTED
            assert [item.parts[0].text for item in owner.drain_steering(key)] == ["remote"]
            owner.unregister_run(key)
        finally:
            other_mem.close()


class TestCrossProcess:

    def test_cancel_from_master_process(self, shared_mem):
        ctx = multiprocessing.get_context("fork")
        key = SessionKey("app", "u", "s")
        attached = ctx.Event()
        seen = ctx.Event()

        process = ctx.Process(
            target=_child_attach_and_wait,
            args=(attached, seen, (key.app_name, key.user_id, key.session_id)),
        )
        process.start()
        try:
            assert attached.wait(5.0)
            slot = shared_mem.find(key)
            assert slot is not None
            assert shared_mem.publish_cancel(slot) is True
            assert seen.wait(5.0)
        finally:
            process.join(5.0)
            if process.is_alive():
                process.terminate()
                process.join(1.0)
        assert process.exitcode == 0
