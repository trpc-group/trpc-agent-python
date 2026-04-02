# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for LlmProcessor."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents.core._llm_processor import LlmProcessor
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield


class MockLLMModel(LLMModel):
    _responses: list = []

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-llmproc-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        for r in self._responses:
            yield r

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


@pytest.fixture
def model():
    m = MockLLMModel(model_name="test-llmproc-model")
    m._responses = [
        LlmResponse(
            content=Content(parts=[Part(text="hello")]),
            partial=False,
        )
    ]
    return m


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = _StubAgent(name="test_agent")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="test_branch",
    )
    return ctx


# ---------------------------------------------------------------------------
# _create_event_from_response
# ---------------------------------------------------------------------------


class TestCreateEventFromResponse:
    def test_maps_response_fields(self, model, invocation_context):
        proc = LlmProcessor(model)
        response = LlmResponse(
            content=Content(parts=[Part(text="test")]),
            partial=False,
            error_code="",
            error_message="",
        )
        event = proc._create_event_from_response(invocation_context, "evt-1", response)
        assert event.id == "evt-1"
        assert event.invocation_id == "inv-1"
        assert event.author == "test_agent"
        assert event.branch == "test_branch"
        assert event.content.parts[0].text == "test"
        assert event.partial is False

    def test_preserves_error_fields(self, model, invocation_context):
        proc = LlmProcessor(model)
        response = LlmResponse(
            content=None,
            partial=False,
            error_code="validation_error",
            error_message="Bad request",
        )
        event = proc._create_event_from_response(invocation_context, "evt-2", response)
        assert event.error_code == "validation_error"
        assert event.error_message == "Bad request"


# ---------------------------------------------------------------------------
# _create_error_event
# ---------------------------------------------------------------------------


class TestCreateErrorEvent:
    def test_creates_error_event(self, model, invocation_context):
        proc = LlmProcessor(model)
        event = proc._create_error_event(invocation_context, "err_code", "err_msg")
        assert event.error_code == "err_code"
        assert event.error_message == "err_msg"
        assert event.author == "test_agent"
        assert event.invocation_id == "inv-1"


# ---------------------------------------------------------------------------
# _process_planning_response
# ---------------------------------------------------------------------------


class TestProcessPlanningResponse:
    def test_no_planner_returns_event_unchanged(self, model, invocation_context):
        proc = LlmProcessor(model)
        event = Event(
            invocation_id="inv-1",
            author="test",
            content=Content(parts=[Part(text="hello")]),
        )
        result = proc._process_planning_response(event, invocation_context)
        assert result is event

    def test_event_without_content_skips_planning(self, model, invocation_context):
        proc = LlmProcessor(model)
        event = Event(invocation_id="inv-1", author="test", content=None)
        result = proc._process_planning_response(event, invocation_context)
        assert result is event


# ---------------------------------------------------------------------------
# call_llm_async
# ---------------------------------------------------------------------------


class TestCallLlmAsync:
    def test_yields_events_for_responses(self, model, invocation_context):
        proc = LlmProcessor(model)
        request = LlmRequest()

        async def run():
            events = []
            async for event in proc.call_llm_async(request, invocation_context, stream=True):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) >= 1
        assert events[0].content.parts[0].text == "hello"

    def test_validation_error_yields_error_event(self, invocation_context):
        m = MockLLMModel(model_name="test-llmproc-model")
        m.validate_request = Mock(side_effect=ValueError("bad request"))
        proc = LlmProcessor(m)
        request = LlmRequest()

        async def run():
            events = []
            async for event in proc.call_llm_async(request, invocation_context, stream=True):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0].error_code == "validation_error"

    def test_streaming_partial_and_final(self, invocation_context):
        m = MockLLMModel(model_name="test-llmproc-model")
        m._responses = [
            LlmResponse(content=Content(parts=[Part(text="part1")]), partial=True),
            LlmResponse(content=Content(parts=[Part(text="part1 part2")]), partial=False),
        ]
        proc = LlmProcessor(m)
        request = LlmRequest()

        async def run():
            events = []
            async for event in proc.call_llm_async(request, invocation_context, stream=True):
                events.append(event)
            return events

        events = asyncio.run(run())
        # Filter out any error events from tracing
        content_events = [e for e in events if e.content is not None and not e.is_error()]
        assert len(content_events) == 2
        assert content_events[0].partial is True
        assert content_events[1].partial is False
