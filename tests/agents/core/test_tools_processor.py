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
import trpc_agent_sdk.skills as _skills_pkg

if not hasattr(_skills_pkg, "get_skill_processor_parameters"):

    def _compat_get_skill_processor_parameters(agent_context):
        from trpc_agent_sdk.agents.core._skill_processor import get_skill_processor_parameters as _impl
        return _impl(agent_context)

    _skills_pkg.get_skill_processor_parameters = _compat_get_skill_processor_parameters

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents.core._tools_processor import ToolsProcessor
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event, EventActions
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import BaseTool, FunctionTool, StreamingProgressTool
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


# ---------------------------------------------------------------------------
# execute_tools_async - progress-streaming tool path
# ---------------------------------------------------------------------------


async def _crawl_stream(url: str):
    """Async generator that yields N+1 progress events."""
    yield {"status": "started", "url": url}
    yield {"status": "step", "i": 1}
    yield {"status": "step", "i": 2}
    yield {"status": "done", "url": url, "steps": 2}


class TestExecuteToolsStreamingProgress:
    """execute_tools_async should surface partial progress events plus a final
    function_response event for tools that set is_progress_streaming=True."""

    def test_streaming_tool_yields_partials_then_final(self, invocation_context):
        tool = StreamingProgressTool(_crawl_stream)
        proc = ToolsProcessor([tool])
        fc = FunctionCall(id="call-1", name="_crawl_stream", args={"url": "https://x"})

        async def run():
            events = []
            async for event in proc.execute_tools_async([fc], invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())

        # 4 yields → 3 partials (the last yield is reserved for the final event)
        # + 1 final function_response event = 4 events total.
        assert len(events) == 4, f"expected 4 events, got {len(events)}"

        partials = events[:-1]
        final = events[-1]

        for ev in partials:
            assert ev.partial is True
            meta = ev.custom_metadata or {}
            assert meta.get("tool_progress") is True
            assert meta.get("tool_name") == "_crawl_stream"
            assert meta.get("tool_call_id") == "call-1"
            assert ev.content is not None
            # Text part contains JSON serialization of the payload.
            assert ev.content.parts[0].text

        # First partial carries the 'started' payload, second carries i=1, ...
        first_payload = (partials[0].custom_metadata or {}).get("payload")
        assert first_payload == {"status": "started", "url": "https://x"}

        # Final event is a non-partial function_response carrying the LAST yield.
        assert final.partial is not True
        assert final.content is not None
        fr = final.content.parts[0].function_response
        assert fr is not None
        assert fr.name == "_crawl_stream"
        assert fr.id == "call-1"
        assert fr.response == {"status": "done", "url": "https://x", "steps": 2}

    def test_streaming_tool_error_yields_error_event(self, invocation_context):
        async def boom(query: str):
            yield {"status": "started"}
            raise RuntimeError("kaboom")
            yield {"unreachable": True}  # pragma: no cover  # noqa: E501

        tool = StreamingProgressTool(boom)
        proc = ToolsProcessor([tool])
        fc = FunctionCall(id="call-err", name="boom", args={"query": "x"})

        async def run():
            events = []
            async for event in proc.execute_tools_async([fc], invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())
        # We expect: the 'started' partial WAS NOT yielded yet (it's still
        # the buffered value when boom() raises), but a tool_execution_error
        # event SHOULD be produced.
        assert any(ev.error_code == "tool_execution_error" for ev in events)

    def test_streaming_tool_runs_outside_parallel_batch(self, invocation_context):
        # When the agent has parallel_tool_calls=True and the batch mixes a
        # streaming and a non-streaming tool, the legacy parallel path must
        # process the non-streaming tool exactly as before; the streaming
        # tool is handled by a separate, sequential phase that surfaces
        # partial progress events as well as a final function_response.
        streaming = StreamingProgressTool(_crawl_stream)
        non_streaming = FunctionTool(sample_tool)
        proc = ToolsProcessor([streaming, non_streaming])

        # _StubAgent is a pydantic model that doesn't declare
        # parallel_tool_calls; bypass validation to flip the runtime flag.
        object.__setattr__(invocation_context.agent, "parallel_tool_calls", True)
        try:
            fc_stream = FunctionCall(id="c-stream", name="_crawl_stream", args={"url": "https://a"})
            fc_normal = FunctionCall(id="c-normal", name="sample_tool", args={"name": "a", "value": "b"})

            async def run():
                events = []
                async for event in proc.execute_tools_async([fc_stream, fc_normal], invocation_context):
                    events.append(event)
                return events

            events = asyncio.run(run())

            # The non-streaming call is the only thing in the parallel batch,
            # so it surfaces as a single function_response event without
            # interleaving partials.
            non_streaming_finals = [
                ev for ev in events if ev.partial is not True and ev.content and any(
                    p.function_response and p.function_response.id == "c-normal" for p in ev.content.parts)
            ]
            assert len(non_streaming_finals) == 1

            # The streaming call yields partials AND its own final event.
            stream_partials = [
                ev for ev in events
                if ev.partial and (ev.custom_metadata or {}).get("tool_call_id") == "c-stream"
            ]
            stream_finals = [
                ev for ev in events if ev.partial is not True and ev.content and any(
                    p.function_response and p.function_response.id == "c-stream" for p in ev.content.parts)
            ]
            assert stream_partials, "expected at least one partial progress event"
            assert len(stream_finals) == 1
        finally:
            object.__setattr__(invocation_context.agent, "parallel_tool_calls", False)

    def test_streaming_tool_not_found_yields_error_event(self, invocation_context):
        # When the LLM names a streaming tool that doesn't exist, the
        # streaming phase must still surface a tool_not_found error event
        # rather than silently dropping the call. We can't easily fake the
        # "name is a streaming tool but no resolution" case, so we just
        # verify the standard not-found error still works when no streaming
        # tools are registered.
        proc = ToolsProcessor([])
        fc = FunctionCall(id="missing", name="ghost_streaming_tool", args={})

        async def run():
            events = []
            async for ev in proc.execute_tools_async([fc], invocation_context):
                events.append(ev)
            return events

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0].error_code == "tool_not_found"


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
