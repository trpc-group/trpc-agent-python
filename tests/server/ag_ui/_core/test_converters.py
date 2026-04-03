# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for _converters module."""

from __future__ import annotations

import base64
import json
from unittest.mock import Mock

import pytest
from ag_ui.core import (
    AssistantMessage,
    BinaryInputContent,
    FunctionCall,
    SystemMessage,
    TextInputContent,
    ToolCall,
    ToolMessage,
    UserMessage,
)

from trpc_agent_sdk import types
from trpc_agent_sdk.events import Event as TRPCEvent
from trpc_agent_sdk.server.ag_ui._core._converters import (
    _get_binary_attributes,
    _get_text_value,
    _is_binary_content,
    _is_text_content,
    _to_binary_part,
    _to_text_part,
    convert_ag_ui_messages_to_trpc,
    convert_json_patch_to_state,
    convert_message_content_to_parts,
    convert_state_to_json_patch,
    convert_trpc_event_to_ag_ui_message,
    create_error_message,
    extract_text_from_content,
)


class TestGetTextValue:
    def test_with_text_input_content(self):
        item = TextInputContent(type="text", text="hello")
        assert _get_text_value(item) == "hello"

    def test_with_dict(self):
        assert _get_text_value({"text": "world"}) == "world"

    def test_with_dict_missing_key(self):
        assert _get_text_value({}) is None

    def test_with_empty_text_input_content(self):
        item = TextInputContent(type="text", text="")
        assert _get_text_value(item) == ""


class TestGetBinaryAttributes:
    def test_with_binary_input_content(self):
        item = BinaryInputContent(
            type="binary",
            data="aGVsbG8=",
            mime_type="image/png",
            url="http://example.com",
            id="bin-1",
        )
        data, mime_type, url, binary_id = _get_binary_attributes(item)
        assert data == "aGVsbG8="
        assert mime_type == "image/png"
        assert url == "http://example.com"
        assert binary_id == "bin-1"

    def test_with_dict_camel_case_mime_type(self):
        item = {"data": "abc", "mimeType": "text/plain", "url": None, "id": "x"}
        data, mime_type, url, binary_id = _get_binary_attributes(item)
        assert data == "abc"
        assert mime_type == "text/plain"
        assert url is None
        assert binary_id == "x"

    def test_with_dict_snake_case_mime_type(self):
        item = {"data": "abc", "mime_type": "text/plain"}
        _, mime_type, _, _ = _get_binary_attributes(item)
        assert mime_type == "text/plain"

    def test_with_dict_no_mime_type(self):
        item = {"data": "abc"}
        _, mime_type, _, _ = _get_binary_attributes(item)
        assert mime_type is None

    def test_with_empty_dict(self):
        data, mime_type, url, binary_id = _get_binary_attributes({})
        assert data is None
        assert mime_type is None
        assert url is None
        assert binary_id is None


class TestToBinaryPart:
    def test_valid_data(self):
        encoded = base64.b64encode(b"hello").decode()
        part = _to_binary_part(encoded, "text/plain", None, None)
        assert part is not None
        assert part.inline_data.mime_type == "text/plain"
        assert part.inline_data.data == b"hello"

    def test_no_data(self):
        assert _to_binary_part(None, "text/plain", None, None) is None
        assert _to_binary_part("", "text/plain", None, None) is None

    def test_url_present(self):
        encoded = base64.b64encode(b"data").decode()
        assert _to_binary_part(encoded, "text/plain", "http://x.com", None) is None

    def test_binary_id_present(self):
        encoded = base64.b64encode(b"data").decode()
        assert _to_binary_part(encoded, "text/plain", None, "id-1") is None

    def test_no_mime_type(self):
        encoded = base64.b64encode(b"data").decode()
        assert _to_binary_part(encoded, None, None, None) is None
        assert _to_binary_part(encoded, "", None, None) is None

    def test_invalid_base64(self):
        assert _to_binary_part("not-valid-base64!!!", "text/plain", None, None) is None


class TestToTextPart:
    def test_with_text(self):
        part = _to_text_part("hello")
        assert part is not None
        assert part.text == "hello"

    def test_empty_string(self):
        assert _to_text_part("") is None

    def test_none(self):
        assert _to_text_part(None) is None


class TestIsTextContent:
    def test_text_dict(self):
        assert _is_text_content({"type": "text", "text": "hi"}) is True

    def test_non_text_dict(self):
        assert _is_text_content({"type": "binary"}) is False

    def test_text_input_content(self):
        assert _is_text_content(TextInputContent(type="text", text="hi")) is True

    def test_binary_input_content(self):
        assert _is_text_content(BinaryInputContent(type="binary", data="x", mime_type="image/png")) is False

    def test_dict_without_type(self):
        assert _is_text_content({"text": "hi"}) is False


class TestIsBinaryContent:
    def test_binary_dict(self):
        assert _is_binary_content({"type": "binary", "data": "x"}) is True

    def test_non_binary_dict(self):
        assert _is_binary_content({"type": "text"}) is False

    def test_binary_input_content(self):
        assert _is_binary_content(BinaryInputContent(type="binary", data="x", mime_type="image/png")) is True

    def test_text_input_content(self):
        assert _is_binary_content(TextInputContent(type="text", text="x")) is False


class TestConvertTrpcEventToAgUiMessage:
    def test_user_event_with_text(self):
        event = TRPCEvent(
            invocation_id="inv-1",
            author="user",
            content=types.Content(role="user", parts=[types.Part(text="hello")]),
        )
        msg = convert_trpc_event_to_ag_ui_message(event)
        assert isinstance(msg, UserMessage)
        assert msg.content == "hello"
        assert msg.id == "inv-1"

    def test_user_event_multiple_text_parts(self):
        event = TRPCEvent(
            invocation_id="inv-2",
            author="user",
            content=types.Content(
                role="user",
                parts=[types.Part(text="line1"), types.Part(text="line2")],
            ),
        )
        msg = convert_trpc_event_to_ag_ui_message(event)
        assert isinstance(msg, UserMessage)
        assert msg.content == "line1\nline2"

    def test_assistant_event_with_text(self):
        event = TRPCEvent(
            invocation_id="inv-3",
            author="model",
            content=types.Content(
                role="model", parts=[types.Part(text="response")]
            ),
        )
        msg = convert_trpc_event_to_ag_ui_message(event)
        assert isinstance(msg, AssistantMessage)
        assert msg.content == "response"
        assert msg.tool_calls is None

    def test_assistant_event_with_tool_calls(self):
        fc = types.FunctionCall(name="my_tool", args={"a": 1}, id="tc-1")
        event = TRPCEvent(
            invocation_id="inv-4",
            author="model",
            content=types.Content(
                role="model", parts=[types.Part(function_call=fc)]
            ),
        )
        msg = convert_trpc_event_to_ag_ui_message(event)
        assert isinstance(msg, AssistantMessage)
        assert msg.content is None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].id == "tc-1"
        assert msg.tool_calls[0].function.name == "my_tool"
        assert json.loads(msg.tool_calls[0].function.arguments) == {"a": 1}

    def test_assistant_event_with_text_and_tool_calls(self):
        fc = types.FunctionCall(name="tool", args={}, id="tc-2")
        event = TRPCEvent(
            invocation_id="inv-5",
            author="model",
            content=types.Content(
                role="model",
                parts=[types.Part(text="thinking"), types.Part(function_call=fc)],
            ),
        )
        msg = convert_trpc_event_to_ag_ui_message(event)
        assert isinstance(msg, AssistantMessage)
        assert msg.content == "thinking"
        assert len(msg.tool_calls) == 1

    def test_none_content(self):
        event = TRPCEvent(invocation_id="inv-6", author="user", content=None)
        assert convert_trpc_event_to_ag_ui_message(event) is None

    def test_empty_parts(self):
        event = TRPCEvent(
            invocation_id="inv-7",
            author="user",
            content=types.Content(role="user", parts=[]),
        )
        assert convert_trpc_event_to_ag_ui_message(event) is None

    def test_user_event_no_text_parts(self):
        event = TRPCEvent(
            invocation_id="inv-8",
            author="user",
            content=types.Content(
                role="user", parts=[types.Part(inline_data=types.Blob(mime_type="image/png", data=b"x"))]
            ),
        )
        assert convert_trpc_event_to_ag_ui_message(event) is None

    def test_function_call_with_explicit_id(self):
        fc = types.FunctionCall(name="tool", args={"k": "v"}, id="fc-99")
        event = TRPCEvent(
            invocation_id="inv-fb",
            author="model",
            content=types.Content(role="model", parts=[types.Part(function_call=fc)]),
        )
        msg = convert_trpc_event_to_ag_ui_message(event)
        assert isinstance(msg, AssistantMessage)
        assert msg.tool_calls[0].id == "fc-99"

    def test_function_call_without_id_returns_none(self):
        fc = types.FunctionCall(name="tool", args={"k": "v"})
        event = TRPCEvent(
            invocation_id="inv-fb2",
            author="model",
            content=types.Content(role="model", parts=[types.Part(function_call=fc)]),
        )
        result = convert_trpc_event_to_ag_ui_message(event)
        assert result is None


class TestConvertStateToJsonPatch:
    def test_replace_values(self):
        patches = convert_state_to_json_patch({"key1": "val1", "key2": 42})
        assert len(patches) == 2
        assert {"op": "replace", "path": "/key1", "value": "val1"} in patches
        assert {"op": "replace", "path": "/key2", "value": 42} in patches

    def test_remove_values(self):
        patches = convert_state_to_json_patch({"removed_key": None})
        assert patches == [{"op": "remove", "path": "/removed_key"}]

    def test_mixed(self):
        patches = convert_state_to_json_patch({"a": 1, "b": None})
        assert len(patches) == 2

    def test_empty(self):
        assert convert_state_to_json_patch({}) == []


class TestConvertJsonPatchToState:
    def test_replace_op(self):
        patches = [{"op": "replace", "path": "/key", "value": "val"}]
        assert convert_json_patch_to_state(patches) == {"key": "val"}

    def test_add_op(self):
        patches = [{"op": "add", "path": "/new_key", "value": 123}]
        assert convert_json_patch_to_state(patches) == {"new_key": 123}

    def test_remove_op(self):
        patches = [{"op": "remove", "path": "/old_key"}]
        assert convert_json_patch_to_state(patches) == {"old_key": None}

    def test_unknown_op_ignored(self):
        patches = [{"op": "copy", "path": "/x", "from": "/y"}]
        assert convert_json_patch_to_state(patches) == {}

    def test_multiple_ops(self):
        patches = [
            {"op": "add", "path": "/a", "value": 1},
            {"op": "remove", "path": "/b"},
            {"op": "replace", "path": "/c", "value": "x"},
        ]
        assert convert_json_patch_to_state(patches) == {"a": 1, "b": None, "c": "x"}

    def test_empty_patches(self):
        assert convert_json_patch_to_state([]) == {}

    def test_path_with_leading_slash_stripped(self):
        patches = [{"op": "replace", "path": "/nested", "value": True}]
        assert convert_json_patch_to_state(patches) == {"nested": True}


class TestExtractTextFromContent:
    def test_with_text_parts(self):
        content = types.Content(
            role="model",
            parts=[types.Part(text="a"), types.Part(text="b")],
        )
        assert extract_text_from_content(content) == "a\nb"

    def test_empty_parts(self):
        content = types.Content(role="model", parts=[])
        assert extract_text_from_content(content) == ""

    def test_none_content(self):
        assert extract_text_from_content(None) == ""

    def test_mixed_parts(self):
        content = types.Content(
            role="model",
            parts=[
                types.Part(text="hello"),
                types.Part(inline_data=types.Blob(mime_type="image/png", data=b"x")),
                types.Part(text="world"),
            ],
        )
        assert extract_text_from_content(content) == "hello\nworld"

    def test_no_text_parts(self):
        content = types.Content(
            role="model",
            parts=[types.Part(inline_data=types.Blob(mime_type="image/png", data=b"x"))],
        )
        assert extract_text_from_content(content) == ""


class TestCreateErrorMessage:
    def test_without_context(self):
        err = ValueError("bad value")
        assert create_error_message(err) == "ValueError: bad value"

    def test_with_context(self):
        err = RuntimeError("oops")
        assert create_error_message(err, "during processing") == "during processing: RuntimeError - oops"

    def test_custom_exception(self):
        class MyError(Exception):
            pass

        err = MyError("custom")
        assert create_error_message(err) == "MyError: custom"


class TestConvertMessageContentToParts:
    def test_none(self):
        assert convert_message_content_to_parts(None) == []

    def test_string(self):
        parts = convert_message_content_to_parts("hello")
        assert len(parts) == 1
        assert parts[0].text == "hello"

    def test_empty_string(self):
        assert convert_message_content_to_parts("") == []

    def test_list_with_text_dict(self):
        parts = convert_message_content_to_parts([{"type": "text", "text": "hi"}])
        assert len(parts) == 1
        assert parts[0].text == "hi"

    def test_list_with_text_input_content(self):
        item = TextInputContent(type="text", text="hello")
        parts = convert_message_content_to_parts([item])
        assert len(parts) == 1
        assert parts[0].text == "hello"

    def test_list_with_binary_dict(self):
        encoded = base64.b64encode(b"data").decode()
        parts = convert_message_content_to_parts([
            {"type": "binary", "data": encoded, "mimeType": "application/octet-stream"}
        ])
        assert len(parts) == 1
        assert parts[0].inline_data is not None

    def test_list_with_unknown_type_ignored(self):
        parts = convert_message_content_to_parts([{"type": "audio", "data": "x"}])
        assert parts == []

    def test_list_with_empty_text_ignored(self):
        parts = convert_message_content_to_parts([{"type": "text", "text": ""}])
        assert parts == []

    def test_mixed_content(self):
        encoded = base64.b64encode(b"img").decode()
        items = [
            {"type": "text", "text": "caption"},
            {"type": "binary", "data": encoded, "mimeType": "image/png"},
        ]
        parts = convert_message_content_to_parts(items)
        assert len(parts) == 2
        assert parts[0].text == "caption"
        assert parts[1].inline_data is not None


class TestConvertAgUiMessagesToTrpc:
    def test_user_message(self):
        msgs = [UserMessage(id="u1", role="user", content="hi")]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        assert events[0].author == "user"
        assert events[0].content.parts[0].text == "hi"

    def test_system_message(self):
        msgs = [SystemMessage(id="s1", role="system", content="be helpful")]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        assert events[0].author == "system"
        assert events[0].content.parts[0].text == "be helpful"

    def test_assistant_message_with_content(self):
        msgs = [AssistantMessage(id="a1", role="assistant", content="reply")]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        assert events[0].content.role == "model"
        assert events[0].content.parts[0].text == "reply"

    def test_assistant_message_with_tool_calls(self):
        tc = ToolCall(
            id="tc-1",
            type="function",
            function=FunctionCall(name="search", arguments='{"q": "test"}'),
        )
        msgs = [AssistantMessage(id="a2", role="assistant", content=None, tool_calls=[tc])]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        fc_part = events[0].content.parts[0]
        assert fc_part.function_call.name == "search"
        assert fc_part.function_call.args == {"q": "test"}
        assert fc_part.function_call.id == "tc-1"

    def test_assistant_message_with_content_and_tool_calls(self):
        tc = ToolCall(
            id="tc-2",
            type="function",
            function=FunctionCall(name="calc", arguments='{"x": 1}'),
        )
        msgs = [AssistantMessage(id="a3", role="assistant", content="thinking", tool_calls=[tc])]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        parts = events[0].content.parts
        assert parts[0].text == "thinking"
        assert parts[1].function_call.name == "calc"

    def test_tool_message_string_content(self):
        msgs = [ToolMessage(id="t1", role="tool", content="result", tool_call_id="tc-1")]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        fr = events[0].content.parts[0].function_response
        assert fr.name == "tc-1"
        assert fr.response == {"result": "result"}

    def test_tool_message_json_string_content(self):
        msgs = [ToolMessage(id="t2", role="tool", content='{"key": "val"}', tool_call_id="tc-2")]
        events = convert_ag_ui_messages_to_trpc(msgs)
        fr = events[0].content.parts[0].function_response
        assert fr.response == {"result": '{"key": "val"}'}

    def test_empty_messages(self):
        assert convert_ag_ui_messages_to_trpc([]) == []

    def test_multiple_messages(self):
        msgs = [
            UserMessage(id="u1", role="user", content="hi"),
            AssistantMessage(id="a1", role="assistant", content="hello"),
        ]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 2

    def test_user_message_with_empty_content(self):
        msgs = [UserMessage(id="u2", role="user", content="")]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        assert events[0].content is None

    def test_assistant_message_no_content_no_tools(self):
        msgs = [AssistantMessage(id="a4", role="assistant", content=None, tool_calls=None)]
        events = convert_ag_ui_messages_to_trpc(msgs)
        assert len(events) == 1
        assert events[0].content is None
