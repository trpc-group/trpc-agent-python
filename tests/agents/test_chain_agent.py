# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for ChainAgent."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents._chain_agent import ChainAgent
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part


class StubAgent(BaseAgent):
    """Stub agent that yields nothing by default."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        return
        yield


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-chain-.*"]

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


def _make_event(author: str, text: str, invocation_id: str = "inv-1") -> Event:
    return Event(
        invocation_id=invocation_id,
        author=author,
        content=Content(parts=[Part(text=text)]),
    )


def _make_sub_agent(name: str, events: list[Event]) -> StubAgent:
    """Create a stub agent and override run_async to yield given events."""
    agent = StubAgent(name=name)

    async def run_async(ctx):
        for e in events:
            yield e

    object.__setattr__(agent, "run_async", run_async)
    return agent


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = ChainAgent(name="chain")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )
    return ctx


class TestChainAgentSequential:
    def test_no_sub_agents_yields_nothing(self, invocation_context):
        chain = ChainAgent(name="empty_chain")

        async def run():
            events = []
            async for event in chain._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []

    def test_single_sub_agent(self, invocation_context):
        e1 = _make_event("sub1", "hello")
        sub1 = _make_sub_agent("sub1", [e1])
        chain = ChainAgent(name="chain", sub_agents=[sub1])

        async def run():
            events = []
            async for event in chain._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0].author == "sub1"

    def test_multiple_sub_agents_in_order(self, invocation_context):
        e1 = _make_event("sub1", "first")
        e2 = _make_event("sub2", "second")
        e3 = _make_event("sub3", "third")

        sub1 = _make_sub_agent("sub1", [e1])
        sub2 = _make_sub_agent("sub2", [e2])
        sub3 = _make_sub_agent("sub3", [e3])

        chain = ChainAgent(name="chain_ordered", sub_agents=[sub1, sub2, sub3])

        async def run():
            events = []
            async for event in chain._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 3
        assert events[0].author == "sub1"
        assert events[1].author == "sub2"
        assert events[2].author == "sub3"

    def test_sub_agent_multiple_events(self, invocation_context):
        e1 = _make_event("sub1", "event1")
        e2 = _make_event("sub1", "event2")
        sub1 = _make_sub_agent("sub1", [e1, e2])

        chain = ChainAgent(name="chain_multi", sub_agents=[sub1])

        async def run():
            events = []
            async for event in chain._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2
