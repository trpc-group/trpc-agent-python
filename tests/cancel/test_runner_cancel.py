# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Runner integration tests for cancel and steering."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import Mock

import pytest

from trpc_agent_sdk import cancel
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.cancel import _public
from trpc_agent_sdk.cancel import UnixSocketConfig
from trpc_agent_sdk.cancel._unix_socket import _UnixSocketManager
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunLimitException
from trpc_agent_sdk.exceptions import RunLimitType
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part


def _mock_agent(name: str = "test_agent") -> Mock:
    agent = Mock(spec=BaseAgent)
    agent.name = name
    agent.sub_agents = []
    agent.get_subagents = Mock(return_value=[])
    agent.parent_agent = None
    agent.find_agent = Mock(return_value=None)
    return agent


def _user(text: str = "hi") -> Content:
    return Content(role="user", parts=[Part(text=text)])


@pytest.fixture
def sdk_log_propagates():
    """Expose SDK log records to caplog on pytest versions before 9.

    ``DefaultLogger`` sets ``propagate = False``; older pytest releases do not
    attach caplog handlers to non-propagating loggers.
    """
    sdk_logger = logging.getLogger("trpc_agent_sdk")
    original = sdk_logger.propagate
    sdk_logger.propagate = True
    try:
        yield
    finally:
        sdk_logger.propagate = original


async def _run(runner: Runner, session_id: str):
    return [
        event async for event in runner.run_async(
            user_id="u",
            session_id=session_id,
            new_message=_user(),
            run_config=RunConfig(),
        )
    ]


async def _session_events(service: InMemorySessionService, session_id: str):
    session = await service.get_session(app_name="app", user_id="u", session_id=session_id)
    assert session is not None
    return session.events


class TestCancelRun:

    async def test_cancel_failure_logs_error(self, caplog, sdk_log_propagates):
        runner = Runner(app_name="app", agent=_mock_agent(), session_service=InMemorySessionService())

        with caplog.at_level(logging.ERROR, logger="trpc_agent_sdk"):
            success = await runner.cancel_run_async("u", "missing")

        assert success is False
        assert "Cancel failed" in caplog.text
        assert "no active run" in caplog.text

    async def test_cancel_returns_false_without_active_run(self):
        runner = Runner(app_name="app", agent=_mock_agent(), session_service=InMemorySessionService())
        success = await runner.cancel_run_async("u", "missing")
        assert success is False

    async def test_cancel_timeout_over_unix_socket_returns_false(self, caplog, sdk_log_propagates, tmp_path):
        config = UnixSocketConfig(socket_path=str(tmp_path / "cancel.sock"), poll_interval=0.01)
        cancel.release_unix_socket()
        cancel.init_unix_socket(config)
        # A different worker owns the run; the cancelling worker only sees it
        # through the master control plane.
        owner = _UnixSocketManager(config)
        try:
            await owner.register_run(cancel.SessionKey("app", "u", "s"))
            runner = Runner(app_name="app", agent=_mock_agent(), session_service=InMemorySessionService())
            with caplog.at_level(logging.ERROR, logger="trpc_agent_sdk"):
                success = await runner.cancel_run_async("u", "s", timeout=0.2)

            assert success is False
            assert "Cancel failed" in caplog.text
            assert "Cancel timed out after 0.2s" in caplog.text
        finally:
            await owner.unregister_run(cancel.SessionKey("app", "u", "s"))
            cancel.release_unix_socket()

    async def test_cancel_stops_agent_and_persists_marker(self):
        agent = _mock_agent()
        started = asyncio.Event()

        async def agent_run(ctx):
            started.set()
            n = 0
            while True:
                await ctx.raise_if_cancelled()
                yield Event(
                    invocation_id=ctx.invocation_id,
                    author="test_agent",
                    content=Content(parts=[Part(text=f"tick{n}")]),
                )
                n += 1
                await asyncio.sleep(0.01)

        agent.run_async = agent_run
        service = InMemorySessionService()
        runner = Runner(app_name="app", agent=agent, session_service=service)

        task = asyncio.create_task(_run(runner, "s1"))
        await started.wait()
        success = await runner.cancel_run_async("u", "s1")
        assert success is True
        events = await asyncio.wait_for(task, timeout=2.0)

        assert isinstance(events[-1], AgentCancelledEvent)
        session_events = await _session_events(service, "s1")
        assert session_events[-1].get_text() == "Detect user cancel the agent execution."

        # The run handle is closed after the run, so a stale cancel finds
        # nothing.
        success = await runner.cancel_run_async("u", "s1")
        assert success is False

    async def test_concurrent_run_is_rejected_before_appending_user_message(self):
        agent = _mock_agent()
        started = asyncio.Event()
        release = asyncio.Event()

        async def agent_run(ctx):
            started.set()
            await release.wait()
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="done")]),
            )

        agent.run_async = agent_run
        service = InMemorySessionService()
        runner = Runner(app_name="app", agent=agent, session_service=service)

        task = asyncio.create_task(_run(runner, "s-duplicate"))
        await started.wait()
        try:
            with pytest.raises(RuntimeError):
                await _run(runner, "s-duplicate")
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=2.0)

        session_events = await _session_events(service, "s-duplicate")
        user_messages = [event for event in session_events if event.author == "user" and event.get_text() == "hi"]
        assert len(user_messages) == 1

    async def test_cancel_releases_slot_before_terminal_event(self):
        agent = _mock_agent()
        started = asyncio.Event()
        calls = 0

        async def agent_run(ctx):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                while True:
                    await ctx.raise_if_cancelled()
                    yield Event(
                        invocation_id=ctx.invocation_id,
                        author="test_agent",
                        content=Content(parts=[Part(text="tick")]),
                    )
                    await asyncio.sleep(0.01)
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="second run")]),
            )

        agent.run_async = agent_run
        service = InMemorySessionService()
        runner = Runner(app_name="app", agent=agent, session_service=service)

        async def consume_until_cancelled():
            async for event in runner.run_async(
                    user_id="u",
                    session_id="s7",
                    new_message=_user(),
                    run_config=RunConfig(),
            ):
                if isinstance(event, AgentCancelledEvent):
                    # The caller stops here; the slot must already be free.
                    break

        task = asyncio.create_task(consume_until_cancelled())
        await started.wait()
        success = await runner.cancel_run_async("u", "s7")
        assert success is True
        await asyncio.wait_for(task, timeout=2.0)

        events = await asyncio.wait_for(_run(runner, "s7"), timeout=2.0)
        assert events

    async def test_cancel_completes_unmatched_tool_calls(self):
        agent = _mock_agent()
        started = asyncio.Event()

        async def agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(function_call=FunctionCall(id="fc1", name="slow_tool", args={}))]),
            )
            started.set()
            while True:
                await ctx.raise_if_cancelled()
                await asyncio.sleep(0.01)

        agent.run_async = agent_run
        service = InMemorySessionService()
        runner = Runner(app_name="app", agent=agent, session_service=service)

        task = asyncio.create_task(_run(runner, "s2"))
        await started.wait()
        success = await runner.cancel_run_async("u", "s2")
        assert success is True
        await asyncio.wait_for(task, timeout=2.0)

        session_events = await _session_events(service, "s2")
        responses = [response for event in session_events for response in event.get_function_responses()]
        assert [response.id for response in responses] == ["fc1"]
        assert responses[0].response["status"] == "cancelled"
        assert "slow_tool" in responses[0].response["message"]
        assert session_events[-1].get_text() == "Detect user cancel the agent execution."

    async def test_run_limit_completes_unmatched_tool_calls(self):
        agent = _mock_agent()

        async def agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(function_call=FunctionCall(id="fc1", name="slow_tool", args={}))]),
            )
            raise RunLimitException(
                agent_name="test_agent",
                limit_type=RunLimitType.MAX_ITERATIONS,
                configured_value=1,
                observed_value=2,
            )

        agent.run_async = agent_run
        service = InMemorySessionService()
        runner = Runner(app_name="app", agent=agent, session_service=service)

        with pytest.raises(RunLimitException):
            await _run(runner, "s3")

        session_events = await _session_events(service, "s3")
        responses = [response for event in session_events for response in event.get_function_responses()]
        assert [response.id for response in responses] == ["fc1"]
        assert "Run limit exceeded." in responses[0].response["message"]


class TestSteering:

    async def test_steering_failure_logs_error(self, caplog, sdk_log_propagates):
        runner = Runner(app_name="app", agent=_mock_agent(), session_service=InMemorySessionService())

        with caplog.at_level(logging.ERROR, logger="trpc_agent_sdk"):
            status = await runner.send_steering("u", "missing", _user("hello"))

        assert status.ok is False
        assert "Steering failed" in caplog.text
        assert "no active run" in caplog.text

    async def test_send_steering_requires_active_run(self):
        runner = Runner(app_name="app", agent=_mock_agent(), session_service=InMemorySessionService())
        status = await runner.send_steering("u", "missing", _user("hello"))
        assert status.ok is False
        assert status.error_msg == "no active run"

    async def test_steering_reaches_agent_without_persisting_event(self):
        agent = _mock_agent()
        started = asyncio.Event()
        received = []

        async def agent_run(ctx):
            started.set()
            await asyncio.sleep(0.05)
            steering_content = await ctx.consume_steering()
            if steering_content is not None:
                received.append(cancel.mark_steering_content(steering_content))
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="final")]),
            )

        agent.run_async = agent_run
        service = InMemorySessionService()
        runner = Runner(app_name="app", agent=agent, session_service=service)

        task = asyncio.create_task(_run(runner, "s4"))
        await started.wait()
        status = await runner.send_steering("u", "s4", _user("check Shanghai weather instead"))
        assert status.ok
        events = await asyncio.wait_for(task, timeout=2.0)

        assert [event.get_text() for event in events] == ["final"]
        assert len(received) == 1
        assert received[0].parts[0].text == _public._STEERING_PREFIX
        assert received[0].parts[1].text == "check Shanghai weather instead"
        assert received[0].parts[-1].text == _public._STEERING_SUFFIX

        session_events = await _session_events(service, "s4")
        assert all("check Shanghai weather instead" not in event.get_text() for event in session_events)
