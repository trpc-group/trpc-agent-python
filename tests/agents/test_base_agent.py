# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for BaseAgent and _build_action_string_from_events."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent, _build_action_string_from_events
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part


class ConcreteAgent(BaseAgent):
    """Concrete agent implementation for testing."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=Content(parts=[Part(text="test response")]),
        )


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-base-.*"]

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


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test_app", user_id="user-1", session_id="s-1")
    )
    agent = ConcreteAgent(name="test_agent")
    return InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )


# ---------------------------------------------------------------------------
# _build_action_string_from_events
# ---------------------------------------------------------------------------


class TestBuildActionStringFromEvents:
    def test_empty_events(self):
        assert _build_action_string_from_events([]) == ""

    def test_text_content(self):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(text="Hello world")]),
        )
        result = _build_action_string_from_events([event])
        assert "Hello world" in result

    def test_thought_content(self):
        part = Part(text="deep thinking")
        part.thought = True
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[part]),
        )
        result = _build_action_string_from_events([event])
        assert "deep thinking" in result
        assert "[Thought:" in result

    def test_function_call(self):
        fc = FunctionCall(name="my_tool", args={"key": "value"})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_call=fc)]),
        )
        result = _build_action_string_from_events([event])
        assert "[Function Call: my_tool(" in result
        assert "key" in result

    def test_function_response(self):
        fr = FunctionResponse(name="my_tool", response={"result": "ok"})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        result = _build_action_string_from_events([event])
        assert "[Function Response (my_tool):" in result

    def test_max_length_truncation(self):
        long_args = {f"key_{i}": "x" * 50 for i in range(20)}
        fc = FunctionCall(name="my_tool", args=long_args)
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_call=fc)]),
        )
        result = _build_action_string_from_events([event], max_length=100)
        assert "..." in result

    def test_event_without_content(self):
        event = Event(invocation_id="inv-1", author="agent", content=None)
        assert _build_action_string_from_events([event]) == ""

    def test_event_with_empty_parts(self):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[]),
        )
        assert _build_action_string_from_events([event]) == ""

    def test_multiple_events_joined_by_double_newline(self):
        e1 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(text="first")]),
        )
        e2 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(text="second")]),
        )
        result = _build_action_string_from_events([e1, e2])
        assert "first" in result
        assert "second" in result
        assert "\n\n" in result


# ---------------------------------------------------------------------------
# BaseAgent._create_invocation_context
# ---------------------------------------------------------------------------


class TestCreateInvocationContext:
    def test_same_agent_keeps_branch(self, invocation_context):
        agent = invocation_context.agent
        invocation_context.branch = "existing_branch"
        new_ctx = agent._create_invocation_context(invocation_context)
        assert new_ctx.agent is agent
        assert new_ctx.branch == "existing_branch"

    def test_sub_agent_appends_branch(self, invocation_context):
        parent = invocation_context.agent
        child = ConcreteAgent(name="child_agent")

        invocation_context.branch = "parent_branch"
        new_ctx = child._create_invocation_context(invocation_context)
        assert new_ctx.branch == "parent_branch.child_agent"

    def test_no_branch_initializes_with_name(self, invocation_context):
        child = ConcreteAgent(name="child_agent")
        invocation_context.branch = None

        new_ctx = child._create_invocation_context(invocation_context)
        assert new_ctx.branch == "child_agent"


# ---------------------------------------------------------------------------
# BaseAgent.model_post_init
# ---------------------------------------------------------------------------


class TestBaseAgentModelPostInit:
    def test_invalid_filter_name_raises(self):
        with pytest.raises(ValueError, match="not found"):
            ConcreteAgent(name="bad_agent", filters_name=["nonexistent_filter"])

    def test_callback_filter_appended(self):
        agent = ConcreteAgent(name="agent_with_cb")
        # The last filter should be an AgentCallbackFilter
        from trpc_agent_sdk.agents._callback import AgentCallbackFilter

        assert any(isinstance(f, AgentCallbackFilter) for f in agent.filters)


class TestBaseAgentGetSubagents:
    def test_returns_sub_agents_list(self):
        child = ConcreteAgent(name="child")
        parent = ConcreteAgent(name="parent", sub_agents=[child])
        assert parent.get_subagents() == [child]

    def test_empty_sub_agents(self):
        agent = ConcreteAgent(name="solo")
        assert agent.get_subagents() == []
