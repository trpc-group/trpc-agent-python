# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ParallelAgent and related utilities."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents._parallel_agent import (
    ParallelAgent,
    _create_branch_ctx_for_sub_agent,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
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
        return [r"test-parallel-.*"]

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


def _make_event(author: str, text: str) -> Event:
    return Event(
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part(text=text)]),
    )


def _make_sub_agent(name: str, events: list[Event]) -> StubAgent:
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
    agent = ParallelAgent(name="parallel")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )
    return ctx


# ---------------------------------------------------------------------------
# _create_branch_ctx_for_sub_agent
# ---------------------------------------------------------------------------


class TestCreateBranchCtxForSubAgent:
    def test_creates_branch_with_parent_and_sub(self, invocation_context):
        parent = StubAgent(name="parent")
        sub = StubAgent(name="child")
        invocation_context.branch = None

        result = _create_branch_ctx_for_sub_agent(parent, sub, invocation_context)
        assert result.branch == "parent.child"

    def test_appends_to_existing_branch(self, invocation_context):
        parent = StubAgent(name="parent")
        sub = StubAgent(name="child")
        invocation_context.branch = "root"

        result = _create_branch_ctx_for_sub_agent(parent, sub, invocation_context)
        assert result.branch == "root.parent.child"

    def test_returns_new_context_copy(self, invocation_context):
        parent = StubAgent(name="p")
        sub = StubAgent(name="s")

        result = _create_branch_ctx_for_sub_agent(parent, sub, invocation_context)
        assert result is not invocation_context


# ---------------------------------------------------------------------------
# ParallelAgent._run_async_impl
# ---------------------------------------------------------------------------


class TestParallelAgentExecution:
    def test_no_sub_agents_yields_nothing(self, invocation_context):
        parallel = ParallelAgent(name="parallel_empty")

        async def run():
            events = []
            async for event in parallel._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []

    def test_single_sub_agent(self, invocation_context):
        sub1 = _make_sub_agent("sub1", [_make_event("sub1", "result")])
        parallel = ParallelAgent(name="parallel_single", sub_agents=[sub1])

        async def run():
            events = []
            async for event in parallel._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0].author == "sub1"

    def test_multiple_sub_agents_all_yield(self, invocation_context):
        sub1 = _make_sub_agent("sub1", [_make_event("sub1", "r1")])
        sub2 = _make_sub_agent("sub2", [_make_event("sub2", "r2")])

        parallel = ParallelAgent(name="parallel_multi", sub_agents=[sub1, sub2])

        async def run():
            events = []
            async for event in parallel._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2
        authors = {e.author for e in events}
        assert authors == {"sub1", "sub2"}

    def test_sub_agent_multiple_events(self, invocation_context):
        sub1 = _make_sub_agent("sub1", [_make_event("sub1", "e1"), _make_event("sub1", "e2")])

        parallel = ParallelAgent(name="parallel_multi_ev", sub_agents=[sub1])

        async def run():
            events = []
            async for event in parallel._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2
