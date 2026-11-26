# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Unix socket control plane."""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

from trpc_agent_sdk import cancel
from trpc_agent_sdk.cancel import SessionKey
from trpc_agent_sdk.cancel import UnixSocketConfig
from trpc_agent_sdk.cancel import _public
from trpc_agent_sdk.cancel._local_manager import _LocalManager
from trpc_agent_sdk.cancel._unix_socket import _UnixSocketManager
from trpc_agent_sdk.cancel._unix_socket._protocol import _key_to_data
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _text(value: str) -> Content:
    return Content(role="user", parts=[Part(text=value)])


async def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def _drain_eventually(
    client: _UnixSocketManager,
    key: SessionKey,
    timeout: float = 2.0,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        contents = await client.drain_steering(key)
        if contents:
            return contents
        await asyncio.sleep(0.01)
    return []


@pytest_asyncio.fixture
async def socket_config(tmp_path):
    cancel.release_unix_socket()
    config = UnixSocketConfig(
        socket_path=str(tmp_path / "cancel.sock"),
        poll_interval=0.01,
    )
    cancel.init_unix_socket(config)
    try:
        yield config
    finally:
        cancel.release_unix_socket()


class TestConfig:

    def test_defaults(self):
        config = UnixSocketConfig()
        assert config.socket_path == "/tmp/trpc-agent-py/cancel.sock"
        assert config.poll_interval == 0.1
        assert config.timeout == 3.0


class TestLifecycle:

    def test_init_and_release(self, tmp_path):
        cancel.release_unix_socket()
        config = UnixSocketConfig(socket_path=str(tmp_path / "cancel.sock"))
        cancel.init_unix_socket(config)
        try:
            assert _public._unix_server is not None
            assert os.path.exists(config.socket_path)
            assert isinstance(_public._manager, _UnixSocketManager)
        finally:
            cancel.release_unix_socket()
        assert not os.path.exists(config.socket_path)
        assert _public._unix_server is None
        assert isinstance(_public._manager, _LocalManager)


class TestRouting:

    async def test_steering_goes_only_to_owner(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        sender = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)
            assert (await sender.send_steering(key, _text("steer"))).ok

            contents = await _drain_eventually(owner, key)
            assert [item.parts[0].text for item in contents] == ["steer"]
            assert await sender.drain_steering(key) == []
        finally:
            await owner.unregister_run(key)

    async def test_cancel_goes_only_to_owner(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        sender = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)
            owner_event = await owner.get_cancel_event(key)
            assert owner_event is not None

            # cancel_run now waits for the run to be released, so the owner must
            # unregister while the sender is polling, not in ``finally``.
            async def _release_after_cancel():
                assert await _wait_for(owner_event.is_set)
                assert await owner.check_cancel(key) is True
                await owner.unregister_run(key)

            release_task = asyncio.create_task(_release_after_cancel())
            status = await sender.cancel_run(key, timeout=2.0)
            await release_task
            assert status.ok is True
            # The sender never owned the run, so it has no local cancel event.
            assert await sender.get_cancel_event(key) is None
        finally:
            await owner.unregister_run(key)

    async def test_is_running_tracks_lifecycle(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        probe = _UnixSocketManager(socket_config)
        try:

            async def is_running():
                response = await probe._send_once({"op": "is_running", "key": _key_to_data(key)})
                return response["running"]

            assert await is_running() is False
            await owner.register_run(key)
            assert await is_running() is True
            await owner.unregister_run(key)
            assert await is_running() is False
            await owner.register_run(key)
            assert await is_running() is True
        finally:
            await owner.unregister_run(key)

    async def test_cancel_waits_for_cleanup(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        sender = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)

            async def _release():
                await asyncio.sleep(0.05)
                await owner.unregister_run(key)

            release_task = asyncio.create_task(_release())
            status = await sender.cancel_run(key, timeout=2.0)
            await release_task
            assert status.ok is True
        finally:
            await owner.unregister_run(key)

    async def test_cancel_timeout_returns_false(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        sender = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)
            status = await sender.cancel_run(key, timeout=0.2)
            assert status.ok is False
            assert "timed out" in status.error_msg
        finally:
            await owner.unregister_run(key)

    async def test_reregister_after_unregister(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)
            await owner.unregister_run(key)
            await owner.register_run(key)
        finally:
            await owner.unregister_run(key)

    async def test_duplicate_register_does_not_break_owner(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        other = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)
            with pytest.raises(RuntimeError):
                await other.register_run(key)
            assert (await other.send_steering(key, _text("still works"))).ok
            contents = await _drain_eventually(owner, key)
            assert [item.parts[0].text for item in contents] == ["still works"]
        finally:
            await owner.unregister_run(key)


class TestSteeringDrain:

    async def test_local_steering_is_immediate(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)
            assert (await owner.send_steering(key, _text("local"))).ok
            contents = await owner.drain_steering(key)
            assert [item.parts[0].text for item in contents] == ["local"]
        finally:
            await owner.unregister_run(key)

    async def test_consume_drains_master_queue(self, socket_config):
        key = SessionKey("app", "u", "s")
        owner = _UnixSocketManager(socket_config)
        sender = _UnixSocketManager(socket_config)
        try:
            await owner.register_run(key)
            assert (await sender.send_steering(key, _text("first"))).ok
            first = await self._consume(owner, key)
            assert first == ["first"]

            assert (await sender.send_steering(key, _text("second"))).ok
            second = await self._consume(owner, key)
            assert second == ["second"]
            assert await owner.drain_steering(key) == []
        finally:
            await owner.unregister_run(key)

    @staticmethod
    async def _consume(client: _UnixSocketManager, key: SessionKey):
        contents = await _drain_eventually(client, key)
        return [item.parts[0].text for item in contents]


class TestConnectionFailure:

    async def test_server_stop_sets_cancel_event(self, tmp_path):
        cancel.release_unix_socket()
        config = UnixSocketConfig(
            socket_path=str(tmp_path / "cancel.sock"),
            poll_interval=0.01,
        )
        cancel.init_unix_socket(config)
        client = _UnixSocketManager(config)
        key = SessionKey("app", "u", "s")
        try:
            await client.register_run(key)
            event = await client.get_cancel_event(key)
            assert event is not None
            cancel.release_unix_socket()
            assert await _wait_for(event.is_set, timeout=2.0)
        finally:
            await client.unregister_run(key)
