# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.server.agents.dsh._convert import to_trpc_event
from trpc_agent_sdk.server.agents.dsh._protocol import Frame


def _convert(frame, call_names=None):
    return to_trpc_event(
        frame,
        invocation_id="inv-1",
        author="dsh_agent",
        branch=None,
        call_names=call_names if call_names is not None else {},
    )


def test_text_delta_is_partial():
    event = _convert(Frame("delta", {"kind": "text", "text": "hello"}))
    assert event is not None
    assert event.partial is True
    assert event.content.parts[0].text == "hello"
    assert not event.content.parts[0].thought


def test_reasoning_delta_is_thought():
    event = _convert(Frame("delta", {"kind": "reasoning", "text": "thinking"}))
    assert event is not None
    assert event.partial is True
    assert event.content.parts[0].text == "thinking"
    assert event.content.parts[0].thought is True


def test_tool_call_delta_is_ignored():
    event = _convert(Frame("delta", {
        "kind": "tool-call",
        "index": 0,
        "toolCall": {"id": "c1", "name": "lookup", "argumentsDelta": "{"},
    }))
    assert event is None


def test_assistant_message_keeps_text_and_reasoning():
    event = _convert(Frame("event", {
        "type": "assistant/message",
        "seq": 5,
        "data": {
            "message": {
                "content": [
                    {"type": "reasoning", "text": "plan"},
                    {"type": "text", "text": "answer"},
                    {"type": "tool-call", "id": "c1", "name": "lookup", "arguments": "{}"},
                ],
            },
        },
    }))
    assert event is not None
    assert event.partial is False
    assert [part.text for part in event.content.parts] == ["plan", "answer"]
    assert event.content.parts[0].thought is True


def test_tool_call_and_result_are_paired():
    call_names = {}
    call = _convert(Frame("event", {
        "type": "tool/call",
        "seq": 1,
        "data": {"callId": "c1", "name": "lookup", "arguments": '{"key":"v"}'},
    }), call_names)
    assert call is not None
    function_call = call.content.parts[0].function_call
    assert function_call.id == "c1"
    assert function_call.name == "lookup"
    assert function_call.args == {"key": "v"}
    assert call_names == {"c1": "lookup"}

    result = _convert(Frame("event", {
        "type": "tool/result",
        "seq": 2,
        "data": {
            "message": {
                "toolCallId": "c1",
                "content": [{"type": "text", "text": "result text"}],
            },
        },
    }), call_names)
    assert result is not None
    function_response = result.content.parts[0].function_response
    assert function_response.id == "c1"
    assert function_response.name == "lookup"
    assert function_response.response == {"result": "result text"}


def test_non_surface_events_are_ignored():
    assert _convert(Frame("event", {"type": "turn/end", "seq": 9, "data": {}})) is None
    assert _convert(Frame("status", {"status": "idle"})) is None
