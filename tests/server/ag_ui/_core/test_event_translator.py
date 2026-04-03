# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for EventTranslator class."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
from ag_ui.core import (
    CustomEvent,
    EventType,
    StateDeltaEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ThinkingTextMessageContentEvent,
    ThinkingTextMessageEndEvent,
    ThinkingTextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
from trpc_agent_sdk.server.ag_ui._core._event_translator import EventTranslator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect(async_gen):
    """Collect all items from an async generator into a list."""
    items = []
    async for item in async_gen:
        items.append(item)
    return items


def _make_part(text=None, thought=False, function_call=None, function_response=None):
    """Create a mock Part with the given attributes."""
    part = Mock()
    part.text = text
    part.thought = thought
    part.function_call = function_call
    part.function_response = function_response
    return part


def _make_content(parts):
    """Create a mock Content with the given parts."""
    content = Mock()
    content.parts = parts
    return content


def _make_trpc_event(
    *,
    author="agent",
    partial=False,
    timestamp=1000.0,
    invocation_id="inv-1",
    content=None,
    actions=None,
    custom_metadata=None,
    is_streaming_tool_call_return=False,
    function_calls=None,
    function_responses=None,
):
    """Create a mock TRPCEvent with the given attributes."""
    event = Mock()
    event.author = author
    event.partial = partial
    event.timestamp = timestamp
    event.invocation_id = invocation_id
    event.content = content
    event.actions = actions
    event.custom_metadata = custom_metadata
    event.is_streaming_tool_call = Mock(return_value=is_streaming_tool_call_return)
    event.get_function_calls = Mock(return_value=function_calls or [])
    event.get_function_responses = Mock(return_value=function_responses or [])
    return event


def _make_function_call(id="fc-1", name="my_tool", args=None):
    """Create a mock FunctionCall."""
    fc = Mock()
    fc.id = id
    fc.name = name
    fc.args = args or {}
    return fc


def _make_function_response(id="fc-1", name="my_tool", response=None):
    """Create a mock FunctionResponse."""
    fr = Mock()
    fr.id = id
    fr.name = name
    fr.response = response or {"result": "ok"}
    return fr


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_initialization(self):
        translator = EventTranslator()
        assert translator._active_tool_calls == {}
        assert translator._streaming_message_id is None
        assert translator._is_streaming is False
        assert translator._text_was_streamed is False
        assert translator._is_thinking is False
        assert translator._thinking_text == ""
        assert translator.long_running_tool_names == []
        assert translator._streaming_tool_calls == {}
        assert translator._streamed_tool_call_ids == set()

    def test_with_long_running_tool_names(self):
        translator = EventTranslator(long_running_tool_names=["approve", "review"])
        assert translator.long_running_tool_names == ["approve", "review"]

    def test_none_long_running_tool_names_defaults_to_empty(self):
        translator = EventTranslator(long_running_tool_names=None)
        assert translator.long_running_tool_names == []


# ---------------------------------------------------------------------------
# TestTranslate
# ---------------------------------------------------------------------------


class TestTranslate:
    async def test_agent_cancelled_event_closes_stream_silently(self):
        translator = EventTranslator()
        translator._is_streaming = True
        translator._streaming_message_id = "msg-1"

        cancelled = Mock(spec=AgentCancelledEvent)
        cancelled.error_message = "User cancelled"
        cancelled.author = "agent"

        events = await _collect(translator.translate(cancelled, "thread-1", "run-1"))

        end_events = [e for e in events if isinstance(e, TextMessageEndEvent)]
        assert len(end_events) == 1
        assert translator._is_streaming is False

    async def test_user_event_is_skipped(self):
        translator = EventTranslator()
        event = _make_trpc_event(author="user")
        events = await _collect(translator.translate(event, "t", "r"))
        assert events == []

    async def test_partial_text_content_starts_streaming(self):
        translator = EventTranslator()
        parts = [_make_part(text="Hello ")]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator.translate(event, "t", "r"))

        types_ = [type(e) for e in events]
        assert TextMessageStartEvent in types_
        assert TextMessageContentEvent in types_
        assert translator._is_streaming is True

    async def test_final_text_content_not_streamed(self):
        translator = EventTranslator()
        parts = [_make_part(text="Full response")]
        event = _make_trpc_event(partial=False, content=_make_content(parts))

        events = await _collect(translator.translate(event, "t", "r"))

        types_ = [type(e) for e in events]
        assert TextMessageStartEvent in types_
        assert TextMessageContentEvent in types_
        assert TextMessageEndEvent in types_

    async def test_function_calls_emitted(self):
        translator = EventTranslator()
        fc = _make_function_call(id="fc-1", name="search", args={"q": "test"})
        event = _make_trpc_event(function_calls=[fc])

        events = await _collect(translator.translate(event, "t", "r"))

        types_ = [type(e) for e in events]
        assert ToolCallStartEvent in types_
        assert ToolCallArgsEvent in types_
        assert ToolCallEndEvent in types_

    async def test_function_responses_emitted(self):
        translator = EventTranslator()
        fr = _make_function_response(id="fc-1", name="search", response={"data": "ok"})
        event = _make_trpc_event(function_responses=[fr])

        events = await _collect(translator.translate(event, "t", "r"))

        types_ = [type(e) for e in events]
        assert ToolCallResultEvent in types_

    async def test_state_delta_emitted(self):
        translator = EventTranslator()
        actions = Mock()
        actions.state_delta = {"key": "value"}
        event = _make_trpc_event(actions=actions)

        events = await _collect(translator.translate(event, "t", "r"))

        delta_events = [e for e in events if isinstance(e, StateDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta == [{"op": "add", "path": "/key", "value": "value"}]

    async def test_custom_metadata_emitted(self):
        translator = EventTranslator()
        event = _make_trpc_event(custom_metadata={"foo": "bar"})

        events = await _collect(translator.translate(event, "t", "r"))

        custom = [e for e in events if isinstance(e, CustomEvent)]
        assert len(custom) == 1
        assert custom[0].name == "trpc_metadata"
        assert custom[0].value == {"foo": "bar"}
        assert custom[0].timestamp == 1000000

    async def test_streaming_tool_call_event(self):
        translator = EventTranslator()
        fc = _make_function_call(id="stc-1", name="tool_a", args={TOOL_STREAMING_ARGS: '{"partial": true}'})
        parts = [_make_part(function_call=fc)]
        event = _make_trpc_event(
            partial=True,
            is_streaming_tool_call_return=True,
            content=_make_content(parts),
        )

        events = await _collect(translator.translate(event, "t", "r"))

        types_ = [type(e) for e in events]
        assert ToolCallStartEvent in types_
        assert ToolCallArgsEvent in types_

    async def test_function_calls_close_active_text_stream(self):
        translator = EventTranslator()
        translator._is_streaming = True
        translator._streaming_message_id = "msg-1"

        fc = _make_function_call(id="fc-1", name="search")
        event = _make_trpc_event(function_calls=[fc])

        events = await _collect(translator.translate(event, "t", "r"))

        types_ = [type(e) for e in events]
        assert TextMessageEndEvent in types_
        idx_end = next(i for i, e in enumerate(events) if isinstance(e, TextMessageEndEvent))
        idx_start = next(i for i, e in enumerate(events) if isinstance(e, ToolCallStartEvent))
        assert idx_end < idx_start

    async def test_thinking_then_text_finalizes_thinking(self):
        translator = EventTranslator()

        thinking_part = _make_part(text="Let me think...", thought=True)
        event1 = _make_trpc_event(partial=True, content=_make_content([thinking_part]))
        await _collect(translator.translate(event1, "t", "r"))
        assert translator._is_thinking is True

        text_part = _make_part(text="Here is the answer")
        event2 = _make_trpc_event(partial=True, content=_make_content([text_part]))
        events = await _collect(translator.translate(event2, "t", "r"))

        types_ = [type(e) for e in events]
        assert ThinkingTextMessageEndEvent in types_
        assert ThinkingEndEvent in types_
        assert translator._is_thinking is False

    async def test_exception_in_translate_swallowed(self):
        translator = EventTranslator()
        event = Mock(spec=["author"])
        event.author = "agent"
        # Missing attributes will cause AttributeError, which should be swallowed
        events = await _collect(translator.translate(event, "t", "r"))
        assert events == []


# ---------------------------------------------------------------------------
# TestTranslateTextContent
# ---------------------------------------------------------------------------


class TestTranslateTextContent:
    async def test_first_partial_starts_stream(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True)

        events = await _collect(translator._translate_text_content(event, ["Hello "]))

        assert len(events) == 2
        assert isinstance(events[0], TextMessageStartEvent)
        assert events[0].role == "assistant"
        assert isinstance(events[1], TextMessageContentEvent)
        assert events[1].delta == "Hello "
        assert translator._is_streaming is True
        assert translator._text_was_streamed is True

    async def test_subsequent_partial_content_only(self):
        translator = EventTranslator()
        translator._is_streaming = True
        translator._streaming_message_id = "msg-1"
        event = _make_trpc_event(partial=True)

        events = await _collect(translator._translate_text_content(event, ["more text"]))

        assert len(events) == 1
        assert isinstance(events[0], TextMessageContentEvent)
        assert events[0].delta == "more text"
        assert events[0].message_id == "msg-1"

    async def test_final_with_active_stream_sends_end(self):
        translator = EventTranslator()
        translator._is_streaming = True
        translator._streaming_message_id = "msg-1"
        translator._text_was_streamed = True
        event = _make_trpc_event(partial=False)

        events = await _collect(translator._translate_text_content(event, ["final"]))

        end_events = [e for e in events if isinstance(e, TextMessageEndEvent)]
        assert len(end_events) == 1
        assert end_events[0].message_id == "msg-1"
        assert translator._is_streaming is False
        assert translator._streaming_message_id is None
        assert translator._text_was_streamed is False

    async def test_final_without_streaming_delivers_three_events(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=False, invocation_id="inv-99")

        events = await _collect(translator._translate_text_content(event, ["Non-LLM response"]))

        assert len(events) == 3
        assert isinstance(events[0], TextMessageStartEvent)
        assert events[0].message_id == "inv-99"
        assert isinstance(events[1], TextMessageContentEvent)
        assert events[1].delta == "Non-LLM response"
        assert isinstance(events[2], TextMessageEndEvent)

    async def test_final_after_was_streamed_resets_only(self):
        translator = EventTranslator()
        translator._text_was_streamed = True
        translator._is_streaming = False
        event = _make_trpc_event(partial=False)

        events = await _collect(translator._translate_text_content(event, ["accumulated"]))

        # No START/CONTENT/END because _text_was_streamed is True and _is_streaming is False
        assert len(events) == 0
        assert translator._text_was_streamed is False

    async def test_empty_text_parts_returns_nothing(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True)
        events = await _collect(translator._translate_text_content(event, []))
        assert events == []

    async def test_multiple_text_parts_combined(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True)

        events = await _collect(translator._translate_text_content(event, ["Hello ", "world"]))

        content_events = [e for e in events if isinstance(e, TextMessageContentEvent)]
        assert len(content_events) == 1
        assert content_events[0].delta == "Hello world"

    async def test_timestamp_converted_to_ms(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True, timestamp=1234.567)

        events = await _collect(translator._translate_text_content(event, ["text"]))

        for e in events:
            assert e.timestamp == 1234567


# ---------------------------------------------------------------------------
# TestTranslateLroFunctionCalls
# ---------------------------------------------------------------------------


class TestTranslateLroFunctionCalls:
    async def test_lro_with_dict_args(self):
        translator = EventTranslator()
        fc = Mock()
        fc.id = "lro-1"
        fc.name = "approve_action"
        fc.args = {"action": "deploy"}
        lro_event = Mock()
        lro_event.function_call = fc
        lro_event.timestamp = 500.0

        events = await _collect(translator.translate_lro_function_calls(lro_event))

        assert len(events) == 3
        assert isinstance(events[0], ToolCallStartEvent)
        assert events[0].tool_call_id == "lro-1"
        assert events[0].tool_call_name == "approve_action"
        assert isinstance(events[1], ToolCallArgsEvent)
        assert events[1].delta == json.dumps({"action": "deploy"}, ensure_ascii=False)
        assert isinstance(events[2], ToolCallEndEvent)

    async def test_lro_without_args(self):
        translator = EventTranslator()
        fc = Mock()
        fc.id = "lro-2"
        fc.name = "simple_tool"
        fc.args = None
        lro_event = Mock()
        lro_event.function_call = fc
        lro_event.timestamp = 600.0

        events = await _collect(translator.translate_lro_function_calls(lro_event))

        assert len(events) == 2
        assert isinstance(events[0], ToolCallStartEvent)
        assert isinstance(events[1], ToolCallEndEvent)
        assert not any(isinstance(e, ToolCallArgsEvent) for e in events)

    async def test_lro_with_string_args(self):
        translator = EventTranslator()
        fc = Mock()
        fc.id = "lro-3"
        fc.name = "tool"
        fc.args = "raw string args"
        lro_event = Mock()
        lro_event.function_call = fc
        lro_event.timestamp = 700.0

        events = await _collect(translator.translate_lro_function_calls(lro_event))

        args_events = [e for e in events if isinstance(e, ToolCallArgsEvent)]
        assert len(args_events) == 1
        assert args_events[0].delta == "raw string args"

    async def test_lro_no_function_call(self):
        translator = EventTranslator()
        lro_event = Mock()
        lro_event.function_call = None
        lro_event.timestamp = 800.0

        events = await _collect(translator.translate_lro_function_calls(lro_event))
        assert events == []

    async def test_lro_cleans_up_active_tool_calls(self):
        translator = EventTranslator()
        translator._active_tool_calls["lro-1"] = "lro-1"
        fc = Mock()
        fc.id = "lro-1"
        fc.name = "tool"
        fc.args = None
        lro_event = Mock()
        lro_event.function_call = fc
        lro_event.timestamp = 900.0

        await _collect(translator.translate_lro_function_calls(lro_event))

        assert "lro-1" not in translator._active_tool_calls


# ---------------------------------------------------------------------------
# TestTranslateStreamingToolCall
# ---------------------------------------------------------------------------


class TestTranslateStreamingToolCall:
    async def test_first_chunk_emits_start_and_args(self):
        translator = EventTranslator()
        fc = _make_function_call(id="stc-1", name="tool_a", args={TOOL_STREAMING_ARGS: '{"key": "val"}'})
        parts = [_make_part(function_call=fc)]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator._translate_streaming_tool_call(event))

        assert len(events) == 2
        assert isinstance(events[0], ToolCallStartEvent)
        assert events[0].tool_call_id == "stc-1"
        assert events[0].tool_call_name == "tool_a"
        assert isinstance(events[1], ToolCallArgsEvent)
        assert events[1].delta == '{"key": "val"}'
        assert "stc-1" in translator._streaming_tool_calls

    async def test_subsequent_chunk_args_only(self):
        translator = EventTranslator()
        translator._streaming_tool_calls["stc-1"] = 0

        fc = _make_function_call(id="stc-1", name="tool_a", args={TOOL_STREAMING_ARGS: "more data"})
        parts = [_make_part(function_call=fc)]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator._translate_streaming_tool_call(event))

        assert len(events) == 1
        assert isinstance(events[0], ToolCallArgsEvent)
        assert events[0].delta == "more data"

    async def test_skip_long_running_tools(self):
        translator = EventTranslator(long_running_tool_names=["long_tool"])
        fc = _make_function_call(id="stc-2", name="long_tool", args={TOOL_STREAMING_ARGS: "data"})
        parts = [_make_part(function_call=fc)]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator._translate_streaming_tool_call(event))
        assert events == []

    async def test_skip_without_streaming_args(self):
        translator = EventTranslator()
        fc = _make_function_call(id="stc-3", name="tool_b", args={"normal_key": "val"})
        parts = [_make_part(function_call=fc)]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator._translate_streaming_tool_call(event))
        assert events == []

    async def test_skip_parts_without_function_call(self):
        translator = EventTranslator()
        parts = [_make_part(text="just text")]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator._translate_streaming_tool_call(event))
        assert events == []

    async def test_empty_content_returns_nothing(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True, content=None)

        events = await _collect(translator._translate_streaming_tool_call(event))
        assert events == []

    async def test_empty_delta_no_args_event(self):
        translator = EventTranslator()
        fc = _make_function_call(id="stc-4", name="tool_c", args={TOOL_STREAMING_ARGS: ""})
        parts = [_make_part(function_call=fc)]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator._translate_streaming_tool_call(event))

        # START is emitted but ARGS is skipped because delta is empty string (falsy)
        assert len(events) == 1
        assert isinstance(events[0], ToolCallStartEvent)

    async def test_first_chunk_closes_text_stream(self):
        translator = EventTranslator()
        translator._is_streaming = True
        translator._streaming_message_id = "msg-active"

        fc = _make_function_call(id="stc-5", name="tool_d", args={TOOL_STREAMING_ARGS: "data"})
        parts = [_make_part(function_call=fc)]
        event = _make_trpc_event(partial=True, content=_make_content(parts))

        events = await _collect(translator._translate_streaming_tool_call(event))

        types_ = [type(e) for e in events]
        assert TextMessageEndEvent in types_
        end_idx = next(i for i, e in enumerate(events) if isinstance(e, TextMessageEndEvent))
        start_idx = next(i for i, e in enumerate(events) if isinstance(e, ToolCallStartEvent))
        assert end_idx < start_idx


# ---------------------------------------------------------------------------
# TestCloseStreamingToolCalls
# ---------------------------------------------------------------------------


class TestCloseStreamingToolCalls:
    async def test_closes_active_streaming_calls(self):
        translator = EventTranslator()
        translator._streaming_tool_calls = {"stc-1": True, "stc-2": True}

        events = await _collect(translator._close_streaming_tool_calls(2000.0))

        assert len(events) == 2
        assert all(isinstance(e, ToolCallEndEvent) for e in events)
        end_ids = {e.tool_call_id for e in events}
        assert end_ids == {"stc-1", "stc-2"}

    async def test_clears_state_after_close(self):
        translator = EventTranslator()
        translator._streaming_tool_calls = {"stc-1": True}

        await _collect(translator._close_streaming_tool_calls(2000.0))

        assert translator._streaming_tool_calls == {}

    async def test_adds_to_streamed_tool_call_ids(self):
        translator = EventTranslator()
        translator._streaming_tool_calls = {"stc-1": True, "stc-2": True}

        await _collect(translator._close_streaming_tool_calls(2000.0))

        assert translator._streamed_tool_call_ids == {"stc-1", "stc-2"}

    async def test_no_active_calls_no_events(self):
        translator = EventTranslator()
        events = await _collect(translator._close_streaming_tool_calls(2000.0))
        assert events == []

    async def test_timestamp_converted_to_ms(self):
        translator = EventTranslator()
        translator._streaming_tool_calls = {"stc-1": True}

        events = await _collect(translator._close_streaming_tool_calls(1234.567))

        assert events[0].timestamp == 1234567


# ---------------------------------------------------------------------------
# TestTranslateFunctionCalls
# ---------------------------------------------------------------------------


class TestTranslateFunctionCalls:
    async def test_normal_function_call_full_sequence(self):
        translator = EventTranslator()
        fc = _make_function_call(id="fc-1", name="search", args={"q": "test"})

        events = await _collect(translator._translate_function_calls([fc], 100.0))

        assert len(events) == 3
        assert isinstance(events[0], ToolCallStartEvent)
        assert events[0].tool_call_id == "fc-1"
        assert events[0].tool_call_name == "search"
        assert isinstance(events[1], ToolCallArgsEvent)
        assert events[1].delta == json.dumps({"q": "test"}, ensure_ascii=False)
        assert isinstance(events[2], ToolCallEndEvent)

    async def test_skip_long_running_tools(self):
        translator = EventTranslator(long_running_tool_names=["approve"])
        fc = _make_function_call(id="fc-2", name="approve", args={"x": 1})

        events = await _collect(translator._translate_function_calls([fc], 100.0))
        assert events == []

    async def test_skip_already_streamed_tool_calls(self):
        translator = EventTranslator()
        translator._streamed_tool_call_ids = {"fc-3"}
        fc = _make_function_call(id="fc-3", name="tool", args={"x": 1})

        events = await _collect(translator._translate_function_calls([fc], 100.0))
        assert events == []
        assert "fc-3" not in translator._streamed_tool_call_ids

    async def test_no_args_skips_args_event(self):
        translator = EventTranslator()
        fc = _make_function_call(id="fc-4", name="no_args_tool")
        fc.args = None

        events = await _collect(translator._translate_function_calls([fc], 100.0))

        assert len(events) == 2
        assert isinstance(events[0], ToolCallStartEvent)
        assert isinstance(events[1], ToolCallEndEvent)

    async def test_multiple_function_calls(self):
        translator = EventTranslator()
        fc1 = _make_function_call(id="fc-a", name="tool_a", args={"a": 1})
        fc2 = _make_function_call(id="fc-b", name="tool_b", args={"b": 2})

        events = await _collect(translator._translate_function_calls([fc1, fc2], 100.0))

        start_events = [e for e in events if isinstance(e, ToolCallStartEvent)]
        assert len(start_events) == 2
        assert {e.tool_call_id for e in start_events} == {"fc-a", "fc-b"}

    async def test_cleans_up_active_tool_calls(self):
        translator = EventTranslator()
        fc = _make_function_call(id="fc-5", name="tool")

        await _collect(translator._translate_function_calls([fc], 100.0))

        assert "fc-5" not in translator._active_tool_calls

    async def test_string_args_converted(self):
        translator = EventTranslator()
        fc = _make_function_call(id="fc-6", name="tool")
        fc.args = "raw string"

        events = await _collect(translator._translate_function_calls([fc], 100.0))

        args_events = [e for e in events if isinstance(e, ToolCallArgsEvent)]
        assert len(args_events) == 1
        assert args_events[0].delta == "raw string"


# ---------------------------------------------------------------------------
# TestTranslateFunctionResponse
# ---------------------------------------------------------------------------


class TestTranslateFunctionResponse:
    async def test_normal_response(self):
        translator = EventTranslator()
        fr = _make_function_response(id="fc-1", name="search", response={"data": [1, 2, 3]})

        events = await _collect(translator._translate_function_response([fr], 100.0))

        assert len(events) == 1
        assert isinstance(events[0], ToolCallResultEvent)
        assert events[0].tool_call_id == "fc-1"
        assert events[0].content == json.dumps({"data": [1, 2, 3]}, ensure_ascii=False)

    async def test_skip_long_running_tools(self):
        translator = EventTranslator(long_running_tool_names=["long_tool"])
        fr = _make_function_response(id="fc-2", name="long_tool")

        events = await _collect(translator._translate_function_response([fr], 100.0))
        assert events == []

    async def test_multiple_responses(self):
        translator = EventTranslator()
        fr1 = _make_function_response(id="fc-a", name="tool_a", response={"a": 1})
        fr2 = _make_function_response(id="fc-b", name="tool_b", response={"b": 2})

        events = await _collect(translator._translate_function_response([fr1, fr2], 100.0))

        assert len(events) == 2
        assert all(isinstance(e, ToolCallResultEvent) for e in events)

    async def test_timestamp_converted_to_ms(self):
        translator = EventTranslator()
        fr = _make_function_response(id="fc-1", name="tool")

        events = await _collect(translator._translate_function_response([fr], 1234.567))

        assert events[0].timestamp == 1234567


# ---------------------------------------------------------------------------
# TestCreateStateDeltaEvent
# ---------------------------------------------------------------------------


class TestCreateStateDeltaEvent:
    def test_creates_patches_correctly(self):
        translator = EventTranslator()
        result = translator._create_state_delta_event({"key1": "val1", "key2": 42}, 100.0)

        assert isinstance(result, StateDeltaEvent)
        assert result.type == EventType.STATE_DELTA
        assert result.timestamp == 100000
        expected_patches = [
            {"op": "add", "path": "/key1", "value": "val1"},
            {"op": "add", "path": "/key2", "value": 42},
        ]
        assert result.delta == expected_patches

    def test_empty_delta(self):
        translator = EventTranslator()
        result = translator._create_state_delta_event({}, 200.0)

        assert isinstance(result, StateDeltaEvent)
        assert result.delta == []

    def test_nested_value(self):
        translator = EventTranslator()
        result = translator._create_state_delta_event({"nested": {"a": 1, "b": [2, 3]}}, 300.0)

        assert result.delta == [{"op": "add", "path": "/nested", "value": {"a": 1, "b": [2, 3]}}]


# ---------------------------------------------------------------------------
# TestCreateStateSnapshotEvent
# ---------------------------------------------------------------------------


class TestCreateStateSnapshotEvent:
    def test_creates_snapshot_correctly(self):
        translator = EventTranslator()
        snapshot = {"key1": "val1", "key2": 42}
        result = translator._create_state_snapshot_event(snapshot, 100.0)

        assert isinstance(result, StateSnapshotEvent)
        assert result.type == EventType.STATE_SNAPSHOT
        assert result.snapshot == snapshot
        assert result.timestamp == 100000

    def test_empty_snapshot(self):
        translator = EventTranslator()
        result = translator._create_state_snapshot_event({}, 200.0)

        assert isinstance(result, StateSnapshotEvent)
        assert result.snapshot == {}


# ---------------------------------------------------------------------------
# TestForceCloseStreamingMessage
# ---------------------------------------------------------------------------


class TestForceCloseStreamingMessage:
    async def test_when_streaming_emits_end(self):
        translator = EventTranslator()
        translator._is_streaming = True
        translator._streaming_message_id = "msg-1"

        events = await _collect(translator.force_close_streaming_message())

        assert len(events) == 1
        assert isinstance(events[0], TextMessageEndEvent)
        assert events[0].message_id == "msg-1"
        assert translator._is_streaming is False
        assert translator._streaming_message_id is None

    async def test_when_not_streaming_no_events(self):
        translator = EventTranslator()

        events = await _collect(translator.force_close_streaming_message())
        assert events == []

    async def test_when_streaming_but_no_message_id(self):
        translator = EventTranslator()
        translator._is_streaming = True
        translator._streaming_message_id = None

        events = await _collect(translator.force_close_streaming_message())
        assert events == []


# ---------------------------------------------------------------------------
# TestTranslateThinkingContent
# ---------------------------------------------------------------------------


class TestTranslateThinkingContent:
    async def test_first_partial_starts_thinking(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True, timestamp=500.0)

        events = await _collect(translator._translate_thinking_content(event, ["Thinking..."]))

        assert len(events) == 3
        assert isinstance(events[0], ThinkingStartEvent)
        assert isinstance(events[1], ThinkingTextMessageStartEvent)
        assert isinstance(events[2], ThinkingTextMessageContentEvent)
        assert events[2].delta == "Thinking..."
        assert translator._is_thinking is True
        assert translator._thinking_text == "Thinking..."

    async def test_subsequent_partial_content_only(self):
        translator = EventTranslator()
        translator._is_thinking = True
        translator._thinking_text = "prev "
        event = _make_trpc_event(partial=True)

        events = await _collect(translator._translate_thinking_content(event, ["more thought"]))

        assert len(events) == 1
        assert isinstance(events[0], ThinkingTextMessageContentEvent)
        assert events[0].delta == "more thought"
        assert translator._thinking_text == "prev more thought"

    async def test_final_when_thinking_active_ends_it(self):
        translator = EventTranslator()
        translator._is_thinking = True
        translator._thinking_text = "accumulated"
        event = _make_trpc_event(partial=False)

        events = await _collect(translator._translate_thinking_content(event, ["final thought"]))

        assert len(events) == 2
        assert isinstance(events[0], ThinkingTextMessageEndEvent)
        assert isinstance(events[1], ThinkingEndEvent)
        assert translator._is_thinking is False
        assert translator._thinking_text == ""

    async def test_final_when_not_thinking_skips(self):
        translator = EventTranslator()
        translator._is_thinking = False
        event = _make_trpc_event(partial=False)

        events = await _collect(translator._translate_thinking_content(event, ["thought"]))
        assert events == []

    async def test_empty_thinking_parts_returns_nothing(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True)

        events = await _collect(translator._translate_thinking_content(event, []))
        assert events == []

    async def test_multiple_thinking_parts_combined(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True)

        events = await _collect(translator._translate_thinking_content(event, ["part1 ", "part2"]))

        content_events = [e for e in events if isinstance(e, ThinkingTextMessageContentEvent)]
        assert len(content_events) == 1
        assert content_events[0].delta == "part1 part2"

    async def test_timestamp_converted_to_ms(self):
        translator = EventTranslator()
        event = _make_trpc_event(partial=True, timestamp=1234.567)

        events = await _collect(translator._translate_thinking_content(event, ["thought"]))

        for e in events:
            assert e.timestamp == 1234567


# ---------------------------------------------------------------------------
# TestReset
# ---------------------------------------------------------------------------


class TestReset:
    def test_clears_all_state(self):
        translator = EventTranslator(long_running_tool_names=["tool_x"])
        translator._active_tool_calls = {"fc-1": "fc-1"}
        translator._streaming_message_id = "msg-1"
        translator._is_streaming = True
        translator._text_was_streamed = True
        translator._is_thinking = True
        translator._thinking_text = "some text"
        translator._streaming_tool_calls = {"stc-1": True}
        translator._streamed_tool_call_ids = {"stc-1"}

        translator.reset()

        assert translator._active_tool_calls == {}
        assert translator._streaming_message_id is None
        assert translator._is_streaming is False
        assert translator._text_was_streamed is False
        assert translator._is_thinking is False
        assert translator._thinking_text == ""
        assert translator._streaming_tool_calls == {}
        assert translator._streamed_tool_call_ids == set()

    def test_preserves_long_running_tool_names(self):
        translator = EventTranslator(long_running_tool_names=["tool_x"])
        translator.reset()
        assert translator.long_running_tool_names == ["tool_x"]

    def test_reset_idempotent(self):
        translator = EventTranslator()
        translator.reset()
        translator.reset()

        assert translator._active_tool_calls == {}
        assert translator._is_streaming is False
