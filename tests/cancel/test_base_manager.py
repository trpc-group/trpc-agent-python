# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the pluggable cancel manager contract."""

from __future__ import annotations

import asyncio
from typing import List
from typing import Optional
from unittest.mock import Mock

import pytest

from trpc_agent_sdk import cancel
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.cancel import CancelManager
from trpc_agent_sdk.cancel import SessionKey
from trpc_agent_sdk.cancel import Status
from trpc_agent_sdk.cancel import _public
from trpc_agent_sdk.cancel._local_manager import _LocalManager
from trpc_agent_sdk.cancel._unix_socket import UnixSocketConfig
from trpc_agent_sdk.cancel._unix_socket import _UnixSocketManager
from trpc_agent_sdk.cancel._unix_socket._protocol import _status_from_data
from trpc_agent_sdk.cancel._unix_socket._protocol import _status_to_data
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class _RecordingManager(CancelManager):
    """Cancel manager that records calls for assertions."""

    def __init__(self, error_msg: str = "") -> None:
        self.registered = []
        self.unregistered = []
        self.cancel_timeout = None
        self.cancel_calls = 0
        self.steering = []
        self.check_calls = 0
        self.events = {}
        self.error_msg = error_msg

    async def register_run(self, key: SessionKey) -> None:
        self.registered.append(key)

    async def unregister_run(self, key: SessionKey) -> None:
        self.unregistered.append(key)

    async def cancel_run(self, key: SessionKey, timeout: float = 1.0) -> Status:
        self.cancel_calls += 1
        self.cancel_timeout = timeout
        return Status(ok=True)

    async def send_steering(self, key: SessionKey, content: Content) -> Status:
        if content is None or not content.parts:
            return Status(ok=False, error_msg="steering content is empty")
        if self.error_msg:
            return Status(ok=False, error_msg=self.error_msg)
        self.steering.append(content)
        return Status(ok=True)

    async def drain_steering(self, key: SessionKey) -> List[Content]:
        contents = list(self.steering)
        self.steering.clear()
        return contents

    async def check_cancel(self, key: SessionKey) -> bool:
        self.check_calls += 1
        return False

    async def get_cancel_event(self, key: SessionKey) -> Optional[asyncio.Event]:
        return self.events.setdefault(key, asyncio.Event())


@pytest.fixture
def restore_manager(monkeypatch):
    monkeypatch.setattr(_public, "_manager", _LocalManager())
    yield


def _mock_agent(name: str = "test_agent") -> Mock:
    agent = Mock(spec=BaseAgent)
    agent.name = name
    agent.sub_agents = []
    agent.get_subagents = Mock(return_value=[])
    agent.parent_agent = None
    agent.find_agent = Mock(return_value=None)
    return agent


def test_cancel_manager_is_abstract():
    with pytest.raises(TypeError):
        CancelManager()


def test_builtin_managers_implement_contract():
    assert issubclass(_LocalManager, CancelManager)
    assert issubclass(_UnixSocketManager, CancelManager)
    _UnixSocketManager(UnixSocketConfig(socket_path="/tmp/trpc-agent-test-cancel.sock"))


async def test_public_api_uses_custom_manager(restore_manager):
    manager = _RecordingManager()
    cancel.set_cancel_manager(manager)
    key = SessionKey("app", "u", "s")
    content = Content(role="user", parts=[Part(text="steer")])

    await cancel.register_run(key)
    assert manager.registered == [key]

    status = await cancel.cancel_run(key, timeout=0.25)
    assert status.ok is True
    assert manager.cancel_calls == 1
    assert manager.cancel_timeout == 0.25

    assert (await cancel.send_steering(key, content)).ok
    empty = await cancel.send_steering(key, Content(role="user"))
    assert empty.ok is False
    assert empty.error_msg == "steering content is empty"
    consumed = await cancel.consume_steering(key)
    assert consumed is not None
    assert consumed.role == "user"
    assert consumed.parts[0].text == "steer"

    assert await cancel.get_cancel_event(key) is not None
    assert await cancel.check_cancel(key) is False
    assert manager.check_calls == 1

    await cancel.unregister_run(key)
    assert manager.unregistered == [key]


async def test_custom_manager_error_msg_is_preserved(restore_manager):
    manager = _RecordingManager(error_msg="redis unavailable")
    cancel.set_cancel_manager(manager)
    key = SessionKey("app", "u", "s")
    content = Content(role="user", parts=[Part(text="steer")])

    status = await cancel.send_steering(key, content)

    assert status.ok is False
    assert status.error_msg == "redis unavailable"


def test_status_round_trip():
    status = Status(ok=False, error_msg="redis unavailable")

    assert _status_from_data(_status_to_data(status)) == status


def test_status_bool_matches_ok():
    assert bool(Status(ok=True)) is True
    assert bool(Status(ok=False, error_msg="no active run")) is False


async def test_runner_uses_custom_manager(restore_manager):
    manager = _RecordingManager()
    cancel.set_cancel_manager(manager)

    agent = _mock_agent()
    started = asyncio.Event()

    async def agent_run(ctx):
        started.set()
        yield Event(
            invocation_id=ctx.invocation_id,
            author="test_agent",
            content=Content(parts=[Part(text="done")]),
        )

    agent.run_async = agent_run
    runner = Runner(app_name="app", agent=agent, session_service=InMemorySessionService())

    events = [
        event async for event in runner.run_async(
            user_id="u",
            session_id="s",
            new_message=Content(parts=[Part(text="hi")]),
            run_config=RunConfig(),
        )
    ]

    assert started.is_set()
    assert events
    key = SessionKey("app", "u", "s")
    assert manager.registered == [key]
    assert manager.unregistered == [key]
