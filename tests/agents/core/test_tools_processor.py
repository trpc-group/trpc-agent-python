# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ToolsProcessor."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents.core._tools_processor import ToolsProcessor
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event, EventActions
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import BaseTool, FunctionTool
from trpc_agent_sdk.types import Content, FunctionCall, Part


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-tools-proc-.*"]

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


def sample_tool(name: str, value: str) -> dict:
    """A simple sample tool."""
    return {"result": f"{name}={value}"}


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
# ToolsProcessor initialization
# ---------------------------------------------------------------------------


class TestToolsProcessorInit:
    def test_stores_tools(self):
        tool = FunctionTool(sample_tool)
        proc = ToolsProcessor([tool])
        assert proc.tools == [tool]

    def test_empty_tools(self):
        proc = ToolsProcessor([])
        assert proc.tools == []


# ---------------------------------------------------------------------------
# _find_tool
# ---------------------------------------------------------------------------


class TestFindTool:
    def test_finds_matching_tool(self):
        tool = FunctionTool(sample_tool)
        proc = ToolsProcessor([tool])
        fc = FunctionCall(name="sample_tool", args={})

        async def run():
            return await proc._find_tool(fc, [tool])

        result = asyncio.run(run())
        assert result is tool

    def test_returns_none_for_unknown_tool(self):
        tool = FunctionTool(sample_tool)
        proc = ToolsProcessor([tool])
        fc = FunctionCall(name="nonexistent", args={})

        async def run():
            return await proc._find_tool(fc, [tool])

        result = asyncio.run(run())
        assert result is None


# ---------------------------------------------------------------------------
# find_tool (public method)
# ---------------------------------------------------------------------------


class TestFindToolPublic:
    def test_resolves_and_finds(self, invocation_context):
        tool = FunctionTool(sample_tool)
        proc = ToolsProcessor([tool])
        fc = FunctionCall(name="sample_tool", args={})

        async def run():
            return await proc.find_tool(invocation_context, fc)

        result = asyncio.run(run())
        assert result is not None
        assert result.name == "sample_tool"


# ---------------------------------------------------------------------------
# execute_tools_async - sequential
# ---------------------------------------------------------------------------


class TestExecuteToolsSequential:
    def test_single_tool_call(self, invocation_context):
        tool = FunctionTool(sample_tool)
        proc = ToolsProcessor([tool])
        fc = FunctionCall(id="call-1", name="sample_tool", args={"name": "a", "value": "b"})

        async def run():
            events = []
            async for event in proc.execute_tools_async([fc], invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0].content is not None

    def test_no_tool_calls_yields_nothing(self, invocation_context):
        proc = ToolsProcessor([])

        async def run():
            events = []
            async for event in proc.execute_tools_async([], invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []

    def test_tool_not_found_yields_error(self, invocation_context):
        proc = ToolsProcessor([])
        fc = FunctionCall(id="call-1", name="nonexistent", args={})

        async def run():
            events = []
            async for event in proc.execute_tools_async([fc], invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0].error_code == "tool_not_found"


# ---------------------------------------------------------------------------
# _merge_parallel_function_response_events
# ---------------------------------------------------------------------------


class TestMergeParallelFunctionResponseEvents:
    def test_single_event_returns_as_is(self):
        proc = ToolsProcessor([])
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="user", parts=[Part(text="result")]),
        )
        result = proc._merge_parallel_function_response_events([event])
        assert result is event

    def test_multiple_events_merged(self):
        proc = ToolsProcessor([])
        e1 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="user", parts=[Part(text="r1")]),
        )
        e2 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="user", parts=[Part(text="r2")]),
        )
        result = proc._merge_parallel_function_response_events([e1, e2])
        assert len(result.content.parts) == 2

    def test_empty_events_raises(self):
        proc = ToolsProcessor([])
        with pytest.raises(ValueError):
            proc._merge_parallel_function_response_events([])

    def test_merged_actions(self):
        proc = ToolsProcessor([])
        e1 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="user", parts=[Part(text="r1")]),
            actions=EventActions(state_delta={"key1": "val1"}),
        )
        e2 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="user", parts=[Part(text="r2")]),
            actions=EventActions(state_delta={"key2": "val2"}),
        )
        result = proc._merge_parallel_function_response_events([e1, e2])
        assert result.actions.state_delta.get("key1") == "val1"
        assert result.actions.state_delta.get("key2") == "val2"


# ---------------------------------------------------------------------------
# _create_error_event
# ---------------------------------------------------------------------------


class TestToolsProcessorErrorEvent:
    def test_creates_error_with_function_response(self, invocation_context):
        proc = ToolsProcessor([])
        event = proc._create_error_event(
            invocation_context, "test_error", "Something failed", "call-1", "my_tool"
        )
        assert event.error_code == "test_error"
        assert event.error_message == "Something failed"
        assert event.content is not None
        assert event.content.parts[0].function_response.name == "my_tool"

    def test_error_event_without_tool_info(self, invocation_context):
        proc = ToolsProcessor([])
        event = proc._create_error_event(invocation_context, "err", "msg")
        assert event.content.parts[0].function_response.name == "unknown_tool"


# ---------------------------------------------------------------------------
# _update_streaming_tool_names
# ---------------------------------------------------------------------------


class TestUpdateStreamingToolNames:
    def test_no_streaming_tools(self):
        proc = ToolsProcessor([])
        request = LlmRequest()
        tool = Mock(spec=BaseTool)
        tool.is_streaming = False
        tool.name = "tool1"
        proc._update_streaming_tool_names(request, [tool])
        assert request.streaming_tool_names is None

    def test_streaming_tools_detected(self):
        proc = ToolsProcessor([])
        request = LlmRequest()
        tool = Mock(spec=BaseTool)
        tool.is_streaming = True
        tool.name = "stream_tool"
        proc._update_streaming_tool_names(request, [tool])
        assert "stream_tool" in request.streaming_tool_names
