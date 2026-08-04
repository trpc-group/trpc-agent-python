# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LlmProcessor."""

from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import Mock
from unittest.mock import patch

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
    m._responses = [LlmResponse(
        content=Content(parts=[Part(text="hello")]),
        partial=False,
    )]
    return m


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(service.create_session(app_name="test", user_id="u1", session_id="s1"))
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

    def test_error_response_is_traced_before_consumer_stops(self, invocation_context):
        m = MockLLMModel(model_name="test-llmproc-model")
        m._responses = [
            LlmResponse(
                error_code="STREAMING_ERROR",
                error_message="rate limit exceeded",
                partial=False,
            )
        ]
        proc = LlmProcessor(m)
        request = LlmRequest()

        async def run():
            stream = proc.call_llm_async(request, invocation_context, stream=True)
            event = await anext(stream)
            # The downstream LlmAgent returns immediately for an error event,
            # so tracing and span-context cleanup must be complete at this point.
            mock_trace.assert_called_once()
            span_context.__exit__.assert_called_once()
            await stream.aclose()
            return event

        with patch("trpc_agent_sdk.agents.core._llm_processor.trace_call_llm") as mock_trace, \
             patch("trpc_agent_sdk.agents.core._llm_processor.tracer") as mock_tracer:
            span_context = mock_tracer.start_as_current_span.return_value
            event = asyncio.run(run())

        assert event.error_code == "STREAMING_ERROR"
        assert mock_trace.call_args.args[3].error_message == "rate limit exceeded"

    def test_partial_stream_close_traces_accumulated_text_and_error(self, invocation_context):
        m = MockLLMModel(model_name="test-llmproc-model")
        m._responses = [
            LlmResponse(content=Content(parts=[Part(text="part1")]), partial=True),
            LlmResponse(content=Content(parts=[Part(text="part2")]), partial=True),
        ]
        proc = LlmProcessor(m)
        request = LlmRequest()

        async def run():
            stream = proc.call_llm_async(request, invocation_context, stream=True)
            events = [await anext(stream), await anext(stream)]
            await stream.aclose()
            return events

        with patch("trpc_agent_sdk.agents.core._llm_processor.report_call_llm") as mock_report, \
             patch("trpc_agent_sdk.agents.core._llm_processor.trace_call_llm") as mock_trace, \
             patch("trpc_agent_sdk.agents.core._llm_processor.tracer"):
            events = asyncio.run(run())

        assert all(event.partial is True for event in events)
        mock_trace.assert_called_once()
        assert mock_trace.call_args.args[2] is request
        response = mock_trace.call_args.args[3]
        assert response.error_code == "LlmCallGeneratorExit"
        assert response.error_message == "LLM call stopped with GeneratorExit."
        assert response.interrupted is True
        assert response.partial is True
        assert response.content.role == "model"
        assert response.content.parts[0].text == "part1part2"
        assert response.custom_metadata is None
        assert mock_report.call_args.args[2] is response
        assert mock_report.call_args.kwargs["error_type"] == "LlmCallGeneratorExit"

    def test_partial_stream_close_keeps_latest_function_call_content(self, invocation_context):
        m = MockLLMModel(model_name="test-llmproc-model")
        function_call = Part.from_function_call(name="get_weather_report", args={"city": "Beijing"})
        m._responses = [
            LlmResponse(content=Content(parts=[function_call]), partial=True),
        ]
        proc = LlmProcessor(m)
        request = LlmRequest()

        async def run():
            stream = proc.call_llm_async(request, invocation_context, stream=True)
            event = await anext(stream)
            await stream.aclose()
            return event

        with patch("trpc_agent_sdk.agents.core._llm_processor.report_call_llm"), \
             patch("trpc_agent_sdk.agents.core._llm_processor.trace_call_llm") as mock_trace, \
             patch("trpc_agent_sdk.agents.core._llm_processor.tracer"):
            event = asyncio.run(run())

        assert event.get_function_calls()
        response = mock_trace.call_args.args[3]
        assert response.error_code == "LlmCallGeneratorExit"
        assert response.content.parts[0].function_call.name == "get_weather_report"
        assert response.content.parts[0].function_call.args == {"city": "Beijing"}

    def test_partial_stream_close_joins_thought_and_visible_text(self, invocation_context):
        m = MockLLMModel(model_name="test-llmproc-model")
        thought1 = Part(text="I should call get_")
        thought1.thought = True
        thought2 = Part(text="weather_report function with")
        thought2.thought = True
        m._responses = [
            LlmResponse(content=Content(parts=[thought1]), partial=True),
            LlmResponse(content=Content(parts=[thought2]), partial=True),
            LlmResponse(content=Content(parts=[Part(text="Let me check.")]), partial=True),
        ]
        proc = LlmProcessor(m)
        request = LlmRequest()

        async def run():
            stream = proc.call_llm_async(request, invocation_context, stream=True)
            events = [await anext(stream), await anext(stream), await anext(stream)]
            await stream.aclose()
            return events

        with patch("trpc_agent_sdk.agents.core._llm_processor.report_call_llm"), \
             patch("trpc_agent_sdk.agents.core._llm_processor.trace_call_llm") as mock_trace, \
             patch("trpc_agent_sdk.agents.core._llm_processor.tracer"):
            asyncio.run(run())

        response = mock_trace.call_args.args[3]
        assert response.error_code == "LlmCallGeneratorExit"
        assert response.content.parts[0].text == "I should call get_weather_report function with"
        assert response.content.parts[0].thought is True
        assert response.content.parts[1].text == "Let me check."
        assert not response.content.parts[1].thought

    def test_stream_exception_traces_accumulated_partial_content(self, invocation_context):
        m = MockLLMModel(model_name="test-llmproc-model")
        proc = LlmProcessor(m)
        request = LlmRequest()

        async def failing_generate(request, stream=False, ctx=None):
            yield LlmResponse(content=Content(parts=[Part(text="part1")]), partial=True)
            yield LlmResponse(content=Content(parts=[Part(text="part2")]), partial=True)
            raise RuntimeError("upstream failed")

        async def run():
            stream = proc.call_llm_async(request, invocation_context, stream=True)
            return [await anext(stream), await anext(stream), await anext(stream)]

        with patch.object(m, "generate_async", failing_generate), \
             patch("trpc_agent_sdk.agents.core._llm_processor.report_call_llm") as mock_report, \
             patch("trpc_agent_sdk.agents.core._llm_processor.trace_call_llm") as mock_trace, \
             patch("trpc_agent_sdk.agents.core._llm_processor.tracer"):
            events = asyncio.run(run())

        assert events[0].content.parts[0].text == "part1"
        assert events[1].content.parts[0].text == "part2"
        assert events[2].is_error()
        response = mock_trace.call_args.args[3]
        assert response.error_code == "LLM_CALL_ERROR"
        assert response.error_message == "upstream failed"
        assert response.partial is True
        assert response.content.parts[0].text == "part1part2"
        assert response.custom_metadata == {"error_type": "RuntimeError"}
        assert mock_report.call_args.kwargs["error_type"] == "RuntimeError"
