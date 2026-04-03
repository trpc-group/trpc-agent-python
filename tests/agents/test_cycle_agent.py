# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for CycleAgent."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents._cycle_agent import CycleAgent
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event, EventActions
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part


class StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        return
        yield


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-cycle-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


def _make_event(author: str, text: str, escalate: bool = False) -> Event:
    actions = EventActions()
    actions.escalate = escalate
    return Event(
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part(text=text)]),
        actions=actions,
    )


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = CycleAgent(name="cycle")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )
    return ctx


def _make_counting_agent(name: str, max_count: int = 100):
    """Agent that counts invocations."""
    agent = StubAgent(name=name)
    state = {"count": 0}

    async def run_async(ctx):
        state["count"] += 1
        yield _make_event(name, f"iteration {state['count']}")

    object.__setattr__(agent, "run_async", run_async)
    return agent


def _make_escalating_agent(name: str, escalate_at: int):
    """Agent that escalates at a given iteration."""
    agent = StubAgent(name=name)
    state = {"count": 0}

    async def run_async(ctx):
        state["count"] += 1
        should_escalate = state["count"] >= escalate_at
        event = _make_event(name, f"iter {state['count']}", escalate=should_escalate)
        if should_escalate:
            ctx.actions.escalate = True
        yield event

    object.__setattr__(agent, "run_async", run_async)
    return agent


class TestCycleAgentMaxIterations:
    def test_no_sub_agents_no_iterations(self, invocation_context):
        cycle = CycleAgent(name="cycle", max_iterations=3)

        async def run():
            events = []
            async for event in cycle._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []

    def test_respects_max_iterations(self, invocation_context):
        counter = _make_counting_agent("counter")
        cycle = CycleAgent(name="cycle_max", max_iterations=3, sub_agents=[counter])

        async def run():
            events = []
            async for event in cycle._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 3

    def test_escalate_stops_loop(self, invocation_context):
        escalator = _make_escalating_agent("escalator", escalate_at=2)
        cycle = CycleAgent(name="cycle_esc", max_iterations=10, sub_agents=[escalator])

        async def run():
            events = []
            async for event in cycle._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2

    def test_no_max_iterations_with_escalate(self, invocation_context):
        escalator = _make_escalating_agent("sub", escalate_at=3)
        cycle = CycleAgent(name="cycle_nomax", max_iterations=None, sub_agents=[escalator])

        async def run():
            events = []
            async for event in cycle._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 3


class TestCycleAgentMultipleSubAgents:
    def test_all_sub_agents_run_each_iteration(self, invocation_context):
        order = []

        sub1 = StubAgent(name="sub1")
        sub2 = StubAgent(name="sub2")

        async def sub1_run(ctx):
            order.append("sub1")
            yield _make_event("sub1", "hello")

        async def sub2_run(ctx):
            order.append("sub2")
            yield _make_event("sub2", "world")

        object.__setattr__(sub1, "run_async", sub1_run)
        object.__setattr__(sub2, "run_async", sub2_run)

        cycle = CycleAgent(name="cycle_multi", max_iterations=2, sub_agents=[sub1, sub2])

        async def run():
            events = []
            async for event in cycle._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 4
        assert order == ["sub1", "sub2", "sub1", "sub2"]
