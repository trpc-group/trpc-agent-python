# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for Event class."""

from __future__ import annotations

import uuid

import pytest
from google.genai.types import CodeExecutionResult, ExecutableCode

from trpc_agent_sdk.events._event import Event
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
from trpc_agent_sdk.types import Content, EventActions, FunctionCall, FunctionResponse, Part


# ---------------------------------------------------------------------------
# Event instantiation and model_post_init
# ---------------------------------------------------------------------------


class TestEventCreation:
    def test_default_fields(self):
        event = Event(invocation_id="inv-1", author="agent")
        assert event.invocation_id == "inv-1"
        assert event.author == "agent"
        assert event.id != ""
        assert event.visible is True
        assert event.branch is None
        assert event.partial is None
        assert event.error_code is None
        assert event.error_message is None
        assert event.requires_completion is False
        assert event.version == 0
        assert isinstance(event.actions, EventActions)
        assert event.long_running_tool_ids is None
        assert event.request_id is None
        assert event.parent_invocation_id is None
        assert event.tag is None
        assert event.filter_key is None
        assert event.object is None

    def test_auto_generated_id_is_valid_uuid(self):
        event = Event(invocation_id="inv-1", author="a")
        uuid.UUID(event.id)

    def test_custom_id_preserved(self):
        event = Event(invocation_id="inv-1", author="a", id="custom-id")
        assert event.id == "custom-id"

    def test_empty_id_gets_auto_generated(self):
        event = Event(invocation_id="inv-1", author="a", id="")
        assert event.id != ""

    def test_timestamp_populated(self):
        event = Event(invocation_id="inv-1", author="a")
        assert event.timestamp > 0

    def test_each_event_gets_unique_id(self):
        e1 = Event(invocation_id="inv-1", author="a")
        e2 = Event(invocation_id="inv-1", author="a")
        assert e1.id != e2.id

    def test_optional_fields(self):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            branch="root.child",
            request_id="req-1",
            parent_invocation_id="parent-inv",
            tag="my-tag",
            filter_key="root.child",
            requires_completion=True,
            version=2,
            visible=False,
            object="graph.node.start",
        )
        assert event.branch == "root.child"
        assert event.request_id == "req-1"
        assert event.parent_invocation_id == "parent-inv"
        assert event.tag == "my-tag"
        assert event.filter_key == "root.child"
        assert event.requires_completion is True
        assert event.version == 2
        assert event.visible is False
        assert event.object == "graph.node.start"

    def test_long_running_tool_ids(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            long_running_tool_ids={"tool-1", "tool-2"},
        )
        assert event.long_running_tool_ids == {"tool-1", "tool-2"}


# ---------------------------------------------------------------------------
# Event.new_id
# ---------------------------------------------------------------------------


class TestNewId:
    def test_returns_valid_uuid_string(self):
        new_id = Event.new_id()
        uuid.UUID(new_id)

    def test_unique_ids(self):
        ids = {Event.new_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Event.get_function_calls
# ---------------------------------------------------------------------------


class TestGetFunctionCalls:
    def test_no_content_returns_empty(self):
        event = Event(invocation_id="inv-1", author="a", content=None)
        assert event.get_function_calls() == []

    def test_empty_parts_returns_empty(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[]),
        )
        assert event.get_function_calls() == []

    def test_text_only_returns_empty(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="hello")]),
        )
        assert event.get_function_calls() == []

    def test_single_function_call(self):
        fc = FunctionCall(name="tool_a", args={"k": "v"})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_call=fc)]),
        )
        calls = event.get_function_calls()
        assert len(calls) == 1
        assert calls[0].name == "tool_a"
        assert calls[0].args == {"k": "v"}

    def test_multiple_function_calls(self):
        fc1 = FunctionCall(name="tool_a", args={})
        fc2 = FunctionCall(name="tool_b", args={"x": 1})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_call=fc1), Part(function_call=fc2)]),
        )
        calls = event.get_function_calls()
        assert len(calls) == 2
        assert calls[0].name == "tool_a"
        assert calls[1].name == "tool_b"

    def test_mixed_parts_extracts_only_calls(self):
        fc = FunctionCall(name="tool_a", args={})
        fr = FunctionResponse(name="tool_a", response={"r": 1})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[
                Part(text="hi"),
                Part(function_call=fc),
                Part(function_response=fr),
            ]),
        )
        calls = event.get_function_calls()
        assert len(calls) == 1
        assert calls[0].name == "tool_a"


# ---------------------------------------------------------------------------
# Event.get_function_responses
# ---------------------------------------------------------------------------


class TestGetFunctionResponses:
    def test_no_content_returns_empty(self):
        event = Event(invocation_id="inv-1", author="a", content=None)
        assert event.get_function_responses() == []

    def test_empty_parts_returns_empty(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[]),
        )
        assert event.get_function_responses() == []

    def test_single_function_response(self):
        fr = FunctionResponse(name="tool_a", response={"ok": True})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_response=fr)]),
        )
        responses = event.get_function_responses()
        assert len(responses) == 1
        assert responses[0].name == "tool_a"

    def test_multiple_function_responses(self):
        fr1 = FunctionResponse(name="tool_a", response={})
        fr2 = FunctionResponse(name="tool_b", response={"x": 2})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_response=fr1), Part(function_response=fr2)]),
        )
        assert len(event.get_function_responses()) == 2

    def test_mixed_parts_extracts_only_responses(self):
        fc = FunctionCall(name="tool_a", args={})
        fr = FunctionResponse(name="tool_a", response={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[
                Part(text="hi"),
                Part(function_call=fc),
                Part(function_response=fr),
            ]),
        )
        responses = event.get_function_responses()
        assert len(responses) == 1
        assert responses[0].name == "tool_a"


# ---------------------------------------------------------------------------
# Event.get_text
# ---------------------------------------------------------------------------


class TestGetText:
    def test_no_content_returns_empty(self):
        event = Event(invocation_id="inv-1", author="a", content=None)
        assert event.get_text() == ""

    def test_empty_parts_returns_empty(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[]),
        )
        assert event.get_text() == ""

    def test_single_text_part(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="hello world")]),
        )
        assert event.get_text() == "hello world"

    def test_multiple_text_parts_concatenated(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="hello "), Part(text="world")]),
        )
        assert event.get_text() == "hello world"

    def test_non_text_parts_skipped(self):
        fc = FunctionCall(name="t", args={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="start"), Part(function_call=fc), Part(text="end")]),
        )
        assert event.get_text() == "startend"

    def test_only_function_parts_returns_empty(self):
        fc = FunctionCall(name="t", args={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_call=fc)]),
        )
        assert event.get_text() == ""


# ---------------------------------------------------------------------------
# Event.is_error
# ---------------------------------------------------------------------------


class TestIsError:
    def test_no_error(self):
        event = Event(invocation_id="inv-1", author="a")
        assert event.is_error() is False

    def test_with_error_code(self):
        event = Event(invocation_id="inv-1", author="a", error_code="RATE_LIMIT")
        assert event.is_error() is True

    def test_error_message_without_code_not_error(self):
        event = Event(invocation_id="inv-1", author="a", error_message="oops")
        assert event.is_error() is False


# ---------------------------------------------------------------------------
# Event.has_trailing_code_execution_result
# ---------------------------------------------------------------------------


class TestHasTrailingCodeExecutionResult:
    def test_no_content(self):
        event = Event(invocation_id="inv-1", author="a", content=None)
        assert event.has_trailing_code_execution_result() is False

    def test_empty_parts(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[]),
        )
        assert event.has_trailing_code_execution_result() is False

    def test_text_only(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="code result")]),
        )
        assert event.has_trailing_code_execution_result() is False

    def test_trailing_code_execution_result(self):
        result = CodeExecutionResult(output="42", outcome="OUTCOME_OK")
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[
                Part(text="some text"),
                Part(code_execution_result=result),
            ]),
        )
        assert event.has_trailing_code_execution_result() is True

    def test_non_trailing_code_execution_result(self):
        result = CodeExecutionResult(output="42", outcome="OUTCOME_OK")
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[
                Part(code_execution_result=result),
                Part(text="after"),
            ]),
        )
        assert event.has_trailing_code_execution_result() is False


# ---------------------------------------------------------------------------
# Event.has_trailing_executable_code
# ---------------------------------------------------------------------------


class TestHasTrailingExecutableCode:
    def test_no_content(self):
        event = Event(invocation_id="inv-1", author="a", content=None)
        assert event.has_trailing_executable_code() is False

    def test_empty_parts(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[]),
        )
        assert event.has_trailing_executable_code() is False

    def test_no_executable_code(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="hi")]),
        )
        assert event.has_trailing_executable_code() is False

    def test_with_executable_code(self):
        code = ExecutableCode(code="print(1)", language="PYTHON")
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(executable_code=code)]),
        )
        assert event.has_trailing_executable_code() is True

    def test_executable_code_anywhere_in_parts(self):
        code = ExecutableCode(code="print(1)", language="PYTHON")
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[
                Part(executable_code=code),
                Part(text="after"),
            ]),
        )
        assert event.has_trailing_executable_code() is True


# ---------------------------------------------------------------------------
# Event.is_final_response
# ---------------------------------------------------------------------------


class TestIsFinalResponse:
    def test_plain_text_is_final(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="answer")]),
        )
        assert event.is_final_response() is True

    def test_with_function_calls_not_final(self):
        fc = FunctionCall(name="tool", args={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_call=fc)]),
        )
        assert event.is_final_response() is False

    def test_with_function_responses_not_final(self):
        fr = FunctionResponse(name="tool", response={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_response=fr)]),
        )
        assert event.is_final_response() is False

    def test_partial_event_not_final(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(text="partial")]),
            partial=True,
        )
        assert event.is_final_response() is False

    def test_skip_summarization_is_final(self):
        fc = FunctionCall(name="tool", args={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_call=fc)]),
            actions=EventActions(skip_summarization=True),
        )
        assert event.is_final_response() is True

    def test_long_running_tool_ids_is_final(self):
        fc = FunctionCall(name="tool", args={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            long_running_tool_ids={"tool-1"},
            content=Content(parts=[Part(function_call=fc)]),
        )
        assert event.is_final_response() is True

    def test_no_content_is_final(self):
        event = Event(invocation_id="inv-1", author="a", content=None)
        assert event.is_final_response() is True

    def test_trailing_code_execution_result_not_final(self):
        result = CodeExecutionResult(output="42", outcome="OUTCOME_OK")
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(code_execution_result=result)]),
        )
        assert event.is_final_response() is False

    def test_trailing_executable_code_not_final(self):
        code = ExecutableCode(code="print(1)", language="PYTHON")
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(executable_code=code)]),
        )
        assert event.is_final_response() is False


# ---------------------------------------------------------------------------
# Event.is_streaming_tool_call
# ---------------------------------------------------------------------------


class TestIsStreamingToolCall:
    def test_non_partial_not_streaming(self):
        fc = FunctionCall(name="tool", args={TOOL_STREAMING_ARGS: "delta"})
        event = Event(
            invocation_id="inv-1",
            author="a",
            content=Content(parts=[Part(function_call=fc)]),
            partial=False,
        )
        assert event.is_streaming_tool_call() is False

    def test_partial_none_not_streaming(self):
        event = Event(invocation_id="inv-1", author="a", partial=None)
        assert event.is_streaming_tool_call() is False

    def test_partial_no_content_not_streaming(self):
        event = Event(invocation_id="inv-1", author="a", partial=True, content=None)
        assert event.is_streaming_tool_call() is False

    def test_partial_empty_parts_not_streaming(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            partial=True,
            content=Content(parts=[]),
        )
        assert event.is_streaming_tool_call() is False

    def test_partial_text_only_not_streaming(self):
        event = Event(
            invocation_id="inv-1",
            author="a",
            partial=True,
            content=Content(parts=[Part(text="text")]),
        )
        assert event.is_streaming_tool_call() is False

    def test_partial_function_call_without_streaming_args(self):
        fc = FunctionCall(name="tool", args={"normal": "arg"})
        event = Event(
            invocation_id="inv-1",
            author="a",
            partial=True,
            content=Content(parts=[Part(function_call=fc)]),
        )
        assert event.is_streaming_tool_call() is False

    def test_partial_function_call_with_streaming_args(self):
        fc = FunctionCall(name="tool", args={TOOL_STREAMING_ARGS: "delta_content"})
        event = Event(
            invocation_id="inv-1",
            author="a",
            partial=True,
            content=Content(parts=[Part(function_call=fc)]),
        )
        assert event.is_streaming_tool_call() is True

    def test_partial_function_call_empty_args(self):
        fc = FunctionCall(name="tool", args={})
        event = Event(
            invocation_id="inv-1",
            author="a",
            partial=True,
            content=Content(parts=[Part(function_call=fc)]),
        )
        assert event.is_streaming_tool_call() is False

    def test_partial_function_call_none_args(self):
        fc = FunctionCall(name="tool", args=None)
        event = Event(
            invocation_id="inv-1",
            author="a",
            partial=True,
            content=Content(parts=[Part(function_call=fc)]),
        )
        assert event.is_streaming_tool_call() is False

    def test_multiple_parts_one_streaming(self):
        fc1 = FunctionCall(name="tool_a", args={"normal": True})
        fc2 = FunctionCall(name="tool_b", args={TOOL_STREAMING_ARGS: "delta"})
        event = Event(
            invocation_id="inv-1",
            author="a",
            partial=True,
            content=Content(parts=[Part(function_call=fc1), Part(function_call=fc2)]),
        )
        assert event.is_streaming_tool_call() is True


# ---------------------------------------------------------------------------
# Event model_config / serialization
# ---------------------------------------------------------------------------


class TestEventSerialization:
    def test_camel_case_alias(self):
        event = Event(invocation_id="inv-1", author="a")
        data = event.model_dump(by_alias=True)
        assert "invocationId" in data
        assert "longRunningToolIds" in data

    def test_forbids_extra_fields(self):
        with pytest.raises(Exception):
            Event(invocation_id="inv-1", author="a", unknown_field="x")

    def test_round_trip_json(self):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            branch="root",
            content=Content(parts=[Part(text="test")]),
        )
        json_str = event.model_dump_json(by_alias=True)
        restored = Event.model_validate_json(json_str)
        assert restored.invocation_id == "inv-1"
        assert restored.author == "agent"
        assert restored.branch == "root"
        assert restored.get_text() == "test"
