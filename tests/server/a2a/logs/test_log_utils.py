# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a.logs._log_utils."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    Task,
    TaskState,
    TaskStatus,
)
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from trpc_agent_sdk.server.a2a.logs._log_utils import (
    _is_a2a_data_part,
    _is_a2a_message,
    _is_a2a_task,
    _is_a2a_text_part,
    _metadata_dict,
    build_a2a_request_log,
    build_a2a_response_log,
    build_message_part_log,
)


def _data_part(data: dict) -> Part:
    return Part(data=ParseDict(data, struct_pb2.Value()))


# ---------------------------------------------------------------------------
# Type guard helpers
# ---------------------------------------------------------------------------
class TestIsA2aTask:
    def test_real_task(self):
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        assert _is_a2a_task(task) is True

    def test_non_task(self):
        assert _is_a2a_task("not a task") is False

    def test_duck_type_fallback(self):
        FakeTask = type("Task", (), {"status": "something"})
        obj = FakeTask()
        with patch("trpc_agent_sdk.server.a2a.logs._log_utils.A2ATask", "not_a_type"):
            assert _is_a2a_task(obj) is True


class TestIsA2aMessage:
    def test_real_message(self):
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[])
        assert _is_a2a_message(msg) is True

    def test_non_message(self):
        assert _is_a2a_message(42) is False

    def test_duck_type_fallback(self):
        FakeMessage = type("Message", (), {"role": "agent"})
        obj = FakeMessage()
        with patch("trpc_agent_sdk.server.a2a.logs._log_utils.A2AMessage", "not_a_type"):
            assert _is_a2a_message(obj) is True


# ---------------------------------------------------------------------------
# _metadata_dict
# ---------------------------------------------------------------------------
class TestMetadataDict:
    def test_none_returns_empty(self):
        assert _metadata_dict(None) == {}

    def test_dict_returned_as_is(self):
        payload = {"k": "v"}
        assert _metadata_dict(payload) is payload

    def test_struct_converted(self):
        struct = struct_pb2.Struct()
        struct.update({"k": "v"})
        assert _metadata_dict(struct) == {"k": "v"}


class TestIsA2aTextPart:
    def test_real_text_part(self):
        assert _is_a2a_text_part(Part(text="hello")) is True

    def test_non_text_part(self):
        assert _is_a2a_text_part(_data_part({"k": "v"})) is False


class TestIsA2aDataPart:
    def test_real_data_part(self):
        assert _is_a2a_data_part(_data_part({"k": "v"})) is True

    def test_non_data_part(self):
        assert _is_a2a_data_part(Part(text="hi")) is False


# ---------------------------------------------------------------------------
# build_message_part_log
# ---------------------------------------------------------------------------
class TestBuildMessagePartLog:
    def test_text_part_short(self):
        part = Part(text="short text")
        log = build_message_part_log(part)
        assert "TextPart: short text" in log

    def test_text_part_long_truncated(self):
        long_text = "x" * 200
        part = Part(text=long_text)
        log = build_message_part_log(part)
        assert "..." in log
        assert len(long_text[:100]) == 100

    def test_data_part(self):
        part = _data_part({"name": "tool1", "id": "t1"})
        log = build_message_part_log(part)
        assert "DataPart:" in log
        assert "tool1" in log

    def test_data_part_large_value(self):
        large_dict = {"key": {"nested": "v" * 200}}
        part = _data_part(large_dict)
        log = build_message_part_log(part)
        assert "<dict>" in log

    def test_data_part_scalar_value(self):
        # MessageToDict(Value) of a non-object is a scalar/list, not a dict.
        part = Part(data=ParseDict("scalar", struct_pb2.Value()))
        log = build_message_part_log(part)
        assert "DataPart:" in log
        assert "scalar" in log

    def test_url_part(self):
        part = Part(url="http://example.com/file.png", media_type="image/png")
        log = build_message_part_log(part)
        assert "FilePart:" in log

    def test_raw_part(self):
        part = Part(raw=b"hello", media_type="application/octet-stream")
        log = build_message_part_log(part)
        assert "FilePart: raw bytes (5 bytes)" in log
        assert "application/octet-stream" in log

    def test_metadata_included(self):
        part = Part(text="hi", metadata={"thought": True})
        log = build_message_part_log(part)
        assert "Part Metadata" in log
        assert "thought" in log


# ---------------------------------------------------------------------------
# build_a2a_request_log
# ---------------------------------------------------------------------------
class TestBuildA2aRequestLog:
    def _make_request(self, *, parts=None, configuration=None, metadata=None, msg_metadata=None):
        msg = Message(
            message_id="msg-1",
            role=Role.ROLE_USER,
            parts=parts if parts is not None else [Part(text="hello")],
        )
        if msg_metadata:
            msg.metadata.update(msg_metadata)
        return SendMessageRequest(
            tenant="",
            message=msg,
            configuration=configuration,
            metadata=metadata,
        )

    def test_basic_request(self):
        req = self._make_request()
        log = build_a2a_request_log(req)
        assert "A2A Request:" in log
        assert "msg-1" in log

    def test_request_with_no_parts(self):
        req = self._make_request(parts=[])
        log = build_a2a_request_log(req)
        assert "No parts" in log

    def test_request_with_message_metadata(self):
        req = self._make_request(msg_metadata={"key": "value"})
        log = build_a2a_request_log(req)
        assert "Metadata:" in log

    def test_request_with_params_metadata(self):
        req = self._make_request(metadata={"extra": "data"})
        log = build_a2a_request_log(req)
        assert "Metadata:" in log


# ---------------------------------------------------------------------------
# build_a2a_response_log
# ---------------------------------------------------------------------------
class TestBuildA2aResponseLog:
    def _make_task_response(self, *, status_msg=None, history=None, artifacts=None, metadata=None):
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(
                state=TaskState.TASK_STATE_COMPLETED,
                message=status_msg,
            ),
            history=history,
            artifacts=artifacts,
        )
        if metadata:
            task.metadata.update(metadata)
        return SendMessageResponse(task=task)

    def _make_message_response(self, *, parts=None, metadata=None):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=parts or [Part(text="answer")],
        )
        if metadata:
            msg.metadata.update(metadata)
        return SendMessageResponse(message=msg)

    def test_task_response_basic(self):
        resp = self._make_task_response()
        log = build_a2a_response_log(resp)
        assert "Type: SUCCESS" in log
        assert "Task" in log
        # Protobuf enum values serialize as ints (TASK_STATE_COMPLETED == 3).
        assert f"Status State: {int(TaskState.TASK_STATE_COMPLETED)}" in log

    def test_task_response_with_status_message(self):
        status_msg = Message(
            message_id="sm-1",
            role=Role.ROLE_AGENT,
            parts=[Part(text="done")],
        )
        resp = self._make_task_response(status_msg=status_msg)
        log = build_a2a_response_log(resp)
        assert "sm-1" in log

    def test_task_response_with_history(self):
        history = [
            Message(message_id="h1", role=Role.ROLE_USER, parts=[Part(text="q")]),
            Message(message_id="h2", role=Role.ROLE_AGENT, parts=[Part(text="a")]),
        ]
        resp = self._make_task_response(history=history)
        log = build_a2a_response_log(resp)
        assert "Message 1:" in log
        assert "Message 2:" in log

    def test_task_response_with_metadata(self):
        resp = self._make_task_response(metadata={"key": "val"})
        log = build_a2a_response_log(resp)
        assert "Task Metadata:" in log

    def test_message_response(self):
        resp = self._make_message_response()
        log = build_a2a_response_log(resp)
        assert "Type: SUCCESS" in log
        assert "Message" in log

    def test_message_response_with_metadata(self):
        resp = self._make_message_response(metadata={"k": "v"})
        log = build_a2a_response_log(resp)
        assert "Metadata:" in log

    def test_error_response(self):
        # a2a-sdk 1.x protobuf SendMessageResponse has no error oneof; JSON-RPC
        # errors arrive as JSONRPCErrorResponse (compat) / JSONRPCError.
        from a2a.compat.v0_3.types import JSONRPCErrorResponse

        resp = JSONRPCErrorResponse.model_validate({
            "id": "resp-1",
            "jsonrpc": "2.0",
            "error": {
                "code": -32600,
                "message": "Invalid request",
                "data": {"reason": "bad payload"},
            },
        })
        log = build_a2a_response_log(resp)
        assert "Type: ERROR" in log
        assert "Type: SUCCESS" not in log
        assert "Error Code: -32600" in log
        assert "Invalid request" in log
        assert "bad payload" in log
        assert "Response ID: resp-1" in log
        assert "JSON-RPC: 2.0" in log

    def test_error_response_proto_oneof(self):
        class _Error:
            code = -32001
            message = "Task not found"
            data = None

        class _ProtoErrorResponse:
            error = _Error()
            id = "resp-2"
            jsonrpc = "2.0"

            def HasField(self, name):
                return name == "error"

        log = build_a2a_response_log(_ProtoErrorResponse())
        assert "Type: ERROR" in log
        assert "Error Code: -32001" in log
        assert "Task not found" in log
        assert "Error Data: None" in log

    def test_empty_protobuf_response_is_success(self):
        # 1.x SendMessageResponse has no error field; empty payload is not ERROR.
        log = build_a2a_response_log(SendMessageResponse())
        assert "Type: SUCCESS" in log
        assert "No result" in log
