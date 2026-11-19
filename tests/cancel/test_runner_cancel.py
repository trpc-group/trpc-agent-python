# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Runner integration tests for cancel and steering."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from trpc_agent_sdk import cancel
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.cancel import _public
from trpc_agent_sdk.cancel._shared_manager import _SharedManager
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

    async def test_cancel_returns_false_without_active_run(self):
        runner = Runner(app_name="app", agent=_mock_agent(), session_service=InMemorySessionService())
        assert await runner.cancel_run_async("u", "missing") is False

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
        assert await runner.cancel_run_async("u", "s1") is True
        events = await asyncio.wait_for(task, timeout=2.0)

        assert isinstance(events[-1], AgentCancelledEvent)
        session_events = await _session_events(service, "s1")
        assert session_events[-1].get_text() == "Detect user cancel the agent execution."

        # The run handle is closed after the run, so a stale cancel finds
        # nothing.
        assert await runner.cancel_run_async("u", "s1") is False

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
        assert await runner.cancel_run_async("u", "s7") is True
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
        assert await runner.cancel_run_async("u", "s2") is True
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

    async def test_send_steering_requires_active_run(self):
        runner = Runner(app_name="app", agent=_mock_agent(), session_service=InMemorySessionService())
        assert await runner.send_steering("u", "missing", _user("hello")) is cancel.SteeringStatus.NO_ACTIVE_RUN

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
        assert status is cancel.SteeringStatus.ACCEPTED
        events = await asyncio.wait_for(task, timeout=2.0)

        assert [event.get_text() for event in events] == ["final"]
        assert len(received) == 1
        assert received[0].parts[0].text == _public._STEERING_PREFIX
        assert received[0].parts[1].text == "check Shanghai weather instead"
        assert received[0].parts[-1].text == _public._STEERING_SUFFIX

        session_events = await _session_events(service, "s4")
        assert all("check Shanghai weather instead" not in event.get_text() for event in session_events)


class TestSharedMemoryRunner:

    async def test_runner_uses_shared_control(self):
        cancel.destroy_shared_mem()
        cancel.init_shared_mem(
            cancel.SharedMemoryConfig(
                max_active_runs=4,
                steering_queue_size=2,
                max_steering_bytes=512,
            ))
        try:
            agent = _mock_agent()
            started = asyncio.Event()

            async def agent_run(ctx):
                started.set()
                while True:
                    await ctx.raise_if_cancelled()
                    yield Event(
                        invocation_id=ctx.invocation_id,
                        author="test_agent",
                        content=Content(parts=[Part(text="tick")]),
                    )
                    await asyncio.sleep(0.01)

            agent.run_async = agent_run
            service = InMemorySessionService()
            runner = Runner(app_name="app", agent=agent, session_service=service)

            task = asyncio.create_task(_run(runner, "s6"))
            await started.wait()
            assert isinstance(_public._manager, _SharedManager)
            assert await runner.cancel_run_async("u", "s6") is True
            await asyncio.wait_for(task, timeout=2.0)
        finally:
            cancel.destroy_shared_mem()
