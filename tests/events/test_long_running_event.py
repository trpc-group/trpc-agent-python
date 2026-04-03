# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LongRunningEvent."""

from __future__ import annotations

import uuid

import pytest

from trpc_agent_sdk.events._event import Event
from trpc_agent_sdk.events._long_running_event import LongRunningEvent
from trpc_agent_sdk.types import FunctionCall, FunctionResponse


# ---------------------------------------------------------------------------
# LongRunningEvent creation
# ---------------------------------------------------------------------------


class TestLongRunningEventCreation:
    def test_basic_creation(self):
        fc = FunctionCall(name="long_tool", args={"input": "data"})
        fr = FunctionResponse(name="long_tool", response={"status": "pending"})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="agent",
            function_call=fc,
            function_response=fr,
        )
        assert event.invocation_id == "inv-1"
        assert event.author == "agent"
        assert event.function_call.name == "long_tool"
        assert event.function_call.args == {"input": "data"}
        assert event.function_response.name == "long_tool"
        assert event.function_response.response == {"status": "pending"}

    def test_partial_set_to_true(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert event.partial is True

    def test_partial_overridden_to_true_even_if_set_false(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
            partial=False,
        )
        assert event.partial is True

    def test_requires_function_call(self):
        fr = FunctionResponse(name="tool", response={})
        with pytest.raises(Exception):
            LongRunningEvent(
                invocation_id="inv-1",
                author="a",
                function_response=fr,
            )

    def test_requires_function_response(self):
        fc = FunctionCall(name="tool", args={})
        with pytest.raises(Exception):
            LongRunningEvent(
                invocation_id="inv-1",
                author="a",
                function_call=fc,
            )


# ---------------------------------------------------------------------------
# Inheritance from Event
# ---------------------------------------------------------------------------


class TestLongRunningEventInheritance:
    def test_is_event_instance(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert isinstance(event, Event)

    def test_auto_generated_id(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert event.id != ""
        uuid.UUID(event.id)

    def test_timestamp_populated(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert event.timestamp > 0

    def test_unique_ids_for_different_events(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        e1 = LongRunningEvent(invocation_id="inv-1", author="a", function_call=fc, function_response=fr)
        e2 = LongRunningEvent(invocation_id="inv-1", author="a", function_call=fc, function_response=fr)
        assert e1.id != e2.id


# ---------------------------------------------------------------------------
# Event methods work on LongRunningEvent
# ---------------------------------------------------------------------------


class TestLongRunningEventMethods:
    def test_is_error_false(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert event.is_error() is False

    def test_get_text_empty_when_no_content(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert event.get_text() == ""

    def test_is_final_response_false_because_partial(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert event.is_final_response() is False


# ---------------------------------------------------------------------------
# Additional fields
# ---------------------------------------------------------------------------


class TestLongRunningEventWithOptionalFields:
    def test_with_branch(self):
        fc = FunctionCall(name="tool", args={})
        fr = FunctionResponse(name="tool", response={})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
            branch="root.child",
        )
        assert event.branch == "root.child"

    def test_with_complex_args(self):
        fc = FunctionCall(name="search", args={"query": "test", "limit": 10, "nested": {"key": "val"}})
        fr = FunctionResponse(name="search", response={"results": [1, 2, 3]})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        assert event.function_call.args["nested"]["key"] == "val"
        assert event.function_response.response["results"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestLongRunningEventSerialization:
    def test_camel_case_dump(self):
        fc = FunctionCall(name="tool", args={"k": "v"})
        fr = FunctionResponse(name="tool", response={"r": 1})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="a",
            function_call=fc,
            function_response=fr,
        )
        data = event.model_dump(by_alias=True)
        assert "functionCall" in data
        assert "functionResponse" in data
        assert data["partial"] is True

    def test_round_trip_json(self):
        fc = FunctionCall(name="tool", args={"k": "v"})
        fr = FunctionResponse(name="tool", response={"r": 1})
        event = LongRunningEvent(
            invocation_id="inv-1",
            author="agent",
            function_call=fc,
            function_response=fr,
        )
        json_str = event.model_dump_json(by_alias=True)
        restored = LongRunningEvent.model_validate_json(json_str)
        assert restored.function_call.name == "tool"
        assert restored.function_response.response == {"r": 1}
        assert restored.partial is True
