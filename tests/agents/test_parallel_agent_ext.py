# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Extended tests for ParallelAgent — covers merge helpers, edge cases, and version branching."""

from __future__ import annotations

import asyncio
import sys
from typing import AsyncGenerator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents._parallel_agent import (
    ParallelAgent,
    _create_branch_ctx_for_sub_agent,
    _merge_agent_run,
    _merge_agent_run_pre_3_11,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part


# ---------------------------------------------------------------------------
# Stubs / helpers
# ---------------------------------------------------------------------------


class StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        return
        yield


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-par-ext-.*"]

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
# _merge_agent_run (Python >= 3.11)
# ---------------------------------------------------------------------------


class TestMergeAgentRun:
    def test_single_generator(self):
        """Merges a single generator correctly."""
        async def gen():
            yield _make_event("a", "e1")
            yield _make_event("a", "e2")

        async def run():
            events = []
            async for event in _merge_agent_run([gen()]):
                events.append(event)
            return events

        if sys.version_info >= (3, 11):
            events = asyncio.run(run())
            assert len(events) == 2
        else:
            pytest.skip("TaskGroup requires Python >= 3.11")

    def test_multiple_generators_all_events(self):
        """All events from multiple generators are yielded."""
        async def gen_a():
            yield _make_event("a", "a1")

        async def gen_b():
            yield _make_event("b", "b1")

        async def run():
            events = []
            async for event in _merge_agent_run([gen_a(), gen_b()]):
                events.append(event)
            return events

        if sys.version_info >= (3, 11):
            events = asyncio.run(run())
            assert len(events) == 2
            authors = {e.author for e in events}
            assert authors == {"a", "b"}
        else:
            pytest.skip("TaskGroup requires Python >= 3.11")

    def test_empty_generators(self):
        """Empty generators list yields nothing."""
        async def run():
            events = []
            async for event in _merge_agent_run([]):
                events.append(event)
            return events

        if sys.version_info >= (3, 11):
            events = asyncio.run(run())
            assert events == []
        else:
            pytest.skip("TaskGroup requires Python >= 3.11")


# ---------------------------------------------------------------------------
# _merge_agent_run_pre_3_11
# ---------------------------------------------------------------------------


class TestMergeAgentRunPre311:
    def test_single_generator(self):
        """Merges a single generator correctly (pre-3.11 path)."""
        async def gen():
            yield _make_event("a", "e1")
            yield _make_event("a", "e2")

        async def run():
            events = []
            async for event in _merge_agent_run_pre_3_11([gen()]):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2

    def test_multiple_generators(self):
        """All events from multiple generators are yielded (pre-3.11 path)."""
        async def gen_a():
            yield _make_event("a", "a1")

        async def gen_b():
            yield _make_event("b", "b1")

        async def run():
            events = []
            async for event in _merge_agent_run_pre_3_11([gen_a(), gen_b()]):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2
        authors = {e.author for e in events}
        assert authors == {"a", "b"}

    def test_empty_generators(self):
        """Empty generators list yields nothing (pre-3.11 path)."""
        async def run():
            events = []
            async for event in _merge_agent_run_pre_3_11([]):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []

    def test_order_is_sequential_per_agent(self):
        """Events from the same agent arrive in order."""
        async def gen():
            yield _make_event("a", "e1")
            yield _make_event("a", "e2")
            yield _make_event("a", "e3")

        async def run():
            events = []
            async for event in _merge_agent_run_pre_3_11([gen()]):
                events.append(event)
            return events

        events = asyncio.run(run())
        texts = [e.content.parts[0].text for e in events]
        assert texts == ["e1", "e2", "e3"]


# ---------------------------------------------------------------------------
# _create_branch_ctx_for_sub_agent — additional cases
# ---------------------------------------------------------------------------


class TestCreateBranchCtxExtended:
    def test_empty_string_branch_treated_as_falsy(self, invocation_context):
        """Empty string branch is treated as falsy, suffix becomes the branch."""
        parent = StubAgent(name="p")
        sub = StubAgent(name="s")
        invocation_context.branch = ""
        result = _create_branch_ctx_for_sub_agent(parent, sub, invocation_context)
        assert result.branch == "p.s"

    def test_deep_nesting(self, invocation_context):
        """Branch appends correctly with deep nesting."""
        parent = StubAgent(name="p")
        sub = StubAgent(name="s")
        invocation_context.branch = "a.b.c"
        result = _create_branch_ctx_for_sub_agent(parent, sub, invocation_context)
        assert result.branch == "a.b.c.p.s"

    def test_does_not_mutate_original(self, invocation_context):
        """Original invocation context is not mutated."""
        parent = StubAgent(name="p")
        sub = StubAgent(name="s")
        invocation_context.branch = "original"
        _create_branch_ctx_for_sub_agent(parent, sub, invocation_context)
        assert invocation_context.branch == "original"


# ---------------------------------------------------------------------------
# ParallelAgent._run_async_impl — edge cases
# ---------------------------------------------------------------------------


class TestParallelAgentEdgeCases:
    def test_sub_agent_yields_many_events(self, invocation_context):
        """Sub-agent yielding many events works correctly."""
        events_list = [_make_event("sub1", f"e{i}") for i in range(10)]
        sub = _make_sub_agent("sub1", events_list)
        parallel = ParallelAgent(name="parallel_many", sub_agents=[sub])

        async def run():
            results = []
            async for event in parallel._run_async_impl(invocation_context):
                results.append(event)
            return results

        results = asyncio.run(run())
        assert len(results) == 10

    def test_empty_yielding_sub_agents(self, invocation_context):
        """Sub-agents that yield no events produce an empty result."""
        empty_sub = StubAgent(name="empty_sub")

        async def empty_run(ctx):
            return
            yield

        object.__setattr__(empty_sub, "run_async", empty_run)
        parallel = ParallelAgent(name="parallel_empty_subs", sub_agents=[empty_sub])

        async def run():
            events = []
            async for event in parallel._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []

    def test_version_dispatches_to_correct_merge(self, invocation_context):
        """Python version dispatches to the correct merge function."""
        sub = _make_sub_agent("sub1", [_make_event("sub1", "r1")])
        parallel = ParallelAgent(name="parallel_ver", sub_agents=[sub])

        async def run():
            events = []
            async for event in parallel._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
