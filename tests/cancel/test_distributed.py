# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for distributed cancellation via a shared session flag."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.cancel._cancel import _manager
from trpc_agent_sdk.cancel._cancel import cleanup_run
from trpc_agent_sdk.cancel._cancel import get_cancel_event
from trpc_agent_sdk.cancel._cancel import is_run_cancelled
from trpc_agent_sdk.cancel._cancel import register_run
from trpc_agent_sdk.cancel._distributed import _TRPC_AGENT_CANCEL_KEY
from trpc_agent_sdk.cancel._distributed import start_distributed_cancel
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class FakeSessionService:
    """Minimal session service backed by one mutable session object."""

    def __init__(self, state: dict | None = None):
        self.session = SimpleNamespace(state=state or {})
        self.updates: list[dict] = []
        self.fail_reads = False
        self.fail_writes = False
        self.reads = 0

    async def get_session(self, **_kwargs):
        if self.fail_reads:
            raise RuntimeError("read failed")
        self.reads += 1
        return self.session

    async def update_session_state(self, session, state_delta: dict) -> None:
        if self.fail_writes:
            raise RuntimeError("write failed")
        session.state.update(state_delta)
        self.updates.append(dict(state_delta))


@pytest.fixture(autouse=True)
async def _reset_manager():
    async with _manager._lock:
        _manager._cancelled.clear()
    yield
    async with _manager._lock:
        _manager._cancelled.clear()


def _mock_agent(name: str = "test_agent") -> Mock:
    agent = Mock(spec=BaseAgent)
    agent.name = name
    agent.sub_agents = []
    agent.get_subagents = Mock(return_value=[])
    agent.parent_agent = None
    agent.find_agent = Mock(return_value=None)
    return agent


class TestDistributedWatcher:
    """Tests for _DistributedCancelWatcher and start_distributed_cancel."""

    async def test_start_writes_false_and_aclose_clears(self):
        service = FakeSessionService()
        session_key = await register_run("app", "u1", "s1")

        watcher = await start_distributed_cancel(
            session_service=service,
            session=service.session,
            session_key=session_key,
            poll_interval=0.01,
        )
        assert watcher is not None
        assert service.session.state[_TRPC_AGENT_CANCEL_KEY] is False

        await watcher.aclose()
        assert service.updates[-1] == {_TRPC_AGENT_CANCEL_KEY: None}
        assert _TRPC_AGENT_CANCEL_KEY not in service.session.state

    async def test_watcher_marks_local_run_when_flag_turns_true(self):
        service = FakeSessionService()
        session_key = await register_run("app", "u1", "s1")
        event = await get_cancel_event(session_key)
        assert event is not None and not event.is_set()

        watcher = await start_distributed_cancel(
            session_service=service,
            session=service.session,
            session_key=session_key,
            poll_interval=0.01,
        )
        assert watcher is not None
        try:
            service.session.state[_TRPC_AGENT_CANCEL_KEY] = True
            await asyncio.wait_for(event.wait(), timeout=1.0)
            assert await is_run_cancelled(session_key) is True
        finally:
            await watcher.aclose()
            await cleanup_run("app", "u1", "s1")

    async def test_start_returns_none_when_flag_write_fails(self):
        service = FakeSessionService()
        service.fail_writes = True
        session_key = await register_run("app", "u1", "s1")

        watcher = await start_distributed_cancel(
            session_service=service,
            session=service.session,
            session_key=session_key,
            poll_interval=0.01,
        )
        assert watcher is None
        await cleanup_run("app", "u1", "s1")

    async def test_watcher_survives_read_errors(self):
        service = FakeSessionService()
        service.fail_reads = True
        session_key = await register_run("app", "u1", "s1")
        event = await get_cancel_event(session_key)
        assert event is not None

        watcher = await start_distributed_cancel(
            session_service=service,
            session=service.session,
            session_key=session_key,
            poll_interval=0.01,
        )
        assert watcher is not None
        try:
            await asyncio.sleep(0.05)
            assert not event.is_set()
        finally:
            await watcher.aclose()
            await cleanup_run("app", "u1", "s1")


class TestRunnerDistributedCancel:
    """Tests for Runner.cancel_run_async distributed behavior."""

    async def test_cancel_reads_flag_and_flips_it(self, monkeypatch):
        service = FakeSessionService({_TRPC_AGENT_CANCEL_KEY: False})
        runner = Runner(
            app_name="app",
            agent=_mock_agent(),
            session_service=service,
            enable_distributed_cancel=True,
            cancel_poll_interval=0.01,
        )

        async def _no_local_cancel(*_args, **_kwargs):
            return None

        monkeypatch.setattr("trpc_agent_sdk.cancel.cancel_run", _no_local_cancel)

        assert await runner.cancel_run_async("u1", "s1", timeout=0.5) is True
        assert {_TRPC_AGENT_CANCEL_KEY: True} in service.updates
        assert service.reads == 1

    async def test_cancel_does_not_wait_for_owner(self, monkeypatch):
        service = FakeSessionService({_TRPC_AGENT_CANCEL_KEY: False})
        runner = Runner(
            app_name="app",
            agent=_mock_agent(),
            session_service=service,
            enable_distributed_cancel=True,
            cancel_poll_interval=0.01,
        )

        async def _no_local_cancel(*_args, **_kwargs):
            return None

        monkeypatch.setattr("trpc_agent_sdk.cancel.cancel_run", _no_local_cancel)

        # timeout=0 would expire immediately if the distributed path waited.
        assert await runner.cancel_run_async("u1", "s1", timeout=0) is True
        assert {_TRPC_AGENT_CANCEL_KEY: True} in service.updates
        assert service.reads == 1

    async def test_cancel_returns_false_when_flag_absent(self, monkeypatch):
        service = FakeSessionService()
        runner = Runner(
            app_name="app",
            agent=_mock_agent(),
            session_service=service,
            enable_distributed_cancel=True,
            cancel_poll_interval=0.01,
        )

        async def _no_local_cancel(*_args, **_kwargs):
            return None

        monkeypatch.setattr("trpc_agent_sdk.cancel.cancel_run", _no_local_cancel)

        assert await runner.cancel_run_async("u1", "s1", timeout=0.5) is False
        assert service.updates == []

    async def test_cancel_returns_false_when_distributed_disabled(self, monkeypatch):
        service = FakeSessionService({_TRPC_AGENT_CANCEL_KEY: False})
        runner = Runner(
            app_name="app",
            agent=_mock_agent(),
            session_service=service,
        )

        async def _no_local_cancel(*_args, **_kwargs):
            return None

        monkeypatch.setattr("trpc_agent_sdk.cancel.cancel_run", _no_local_cancel)

        assert await runner.cancel_run_async("u1", "s1", timeout=0.5) is False
        assert service.updates == []

    async def test_invalid_poll_interval_rejected(self):
        with pytest.raises(ValueError, match="cancel_poll_interval"):
            Runner(
                app_name="app",
                agent=_mock_agent(),
                session_service=FakeSessionService(),
                enable_distributed_cancel=True,
                cancel_poll_interval=0,
            )


class TestRunnerFlagLifecycle:
    """Tests that run_async publishes and clears the shared flag."""

    async def test_run_writes_false_and_clears_flag(self):
        service = InMemorySessionService()
        observed: dict[str, object] = {}

        async def agent_run(ctx):
            session = await service.get_session(
                app_name="app",
                user_id="u1",
                session_id="s1",
            )
            observed["during"] = session.state.get(_TRPC_AGENT_CANCEL_KEY)
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="ok")]),
                partial=False,
            )

        agent = _mock_agent()
        agent.run_async = agent_run
        runner = Runner(
            app_name="app",
            agent=agent,
            session_service=service,
            enable_distributed_cancel=True,
            cancel_poll_interval=0.01,
        )

        events = [
            event async for event in runner.run_async(
                user_id="u1",
                session_id="s1",
                new_message=Content(role="user", parts=[Part(text="hi")]),
                run_config=RunConfig(),
            )
        ]
        assert events
        assert observed["during"] is False

        session = await service.get_session(app_name="app", user_id="u1", session_id="s1")
        assert session is not None
        assert session.state.get(_TRPC_AGENT_CANCEL_KEY) is None
