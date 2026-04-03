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
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)

from trpc_agent_sdk.server.a2a.logs._log_utils import (
    _is_a2a_data_part,
    _is_a2a_message,
    _is_a2a_task,
    _is_a2a_text_part,
    build_a2a_request_log,
    build_a2a_response_log,
    build_message_part_log,
)


# ---------------------------------------------------------------------------
# Type guard helpers
# ---------------------------------------------------------------------------
class TestIsA2aTask:
    def test_real_task(self):
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(state=TaskState.completed),
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
        msg = Message(message_id="m1", role=Role.agent, parts=[])
        assert _is_a2a_message(msg) is True

    def test_non_message(self):
        assert _is_a2a_message(42) is False


class TestIsA2aTextPart:
    def test_real_text_part(self):
        assert _is_a2a_text_part(TextPart(text="hello")) is True

    def test_non_text_part(self):
        assert _is_a2a_text_part(DataPart(data={})) is False


class TestIsA2aDataPart:
    def test_real_data_part(self):
        assert _is_a2a_data_part(DataPart(data={"k": "v"})) is True

    def test_non_data_part(self):
        assert _is_a2a_data_part(TextPart(text="hi")) is False


# ---------------------------------------------------------------------------
# build_message_part_log
# ---------------------------------------------------------------------------
class TestBuildMessagePartLog:
    def test_text_part_short(self):
        part = Part(root=TextPart(text="short text"))
        log = build_message_part_log(part)
        assert "TextPart: short text" in log

    def test_text_part_long_truncated(self):
        long_text = "x" * 200
        part = Part(root=TextPart(text=long_text))
        log = build_message_part_log(part)
        assert "..." in log
        assert len(long_text[:100]) == 100

    def test_data_part(self):
        part = Part(root=DataPart(data={"name": "tool1", "id": "t1"}))
        log = build_message_part_log(part)
        assert "DataPart:" in log
        assert "tool1" in log

    def test_data_part_large_value(self):
        large_dict = {"key": {"nested": "v" * 200}}
        part = Part(root=DataPart(data=large_dict))
        log = build_message_part_log(part)
        assert "<dict>" in log

    def test_file_part_fallback(self):
        part = Part(root=FilePart(file=FileWithUri(uri="http://example.com/file.png", mime_type="image/png")))
        log = build_message_part_log(part)
        assert "FilePart:" in log

    def test_metadata_included(self):
        part = Part(root=TextPart(text="hi"))
        part.root.metadata = {"thought": True}
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
            role=Role.user,
            parts=parts if parts is not None else [Part(root=TextPart(text="hello"))],
            metadata=msg_metadata,
        )
        return SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=msg,
                configuration=configuration,
                metadata=metadata,
            ),
        )

    def test_basic_request(self):
        req = self._make_request()
        log = build_a2a_request_log(req)
        assert "A2A Request:" in log
        assert "req-1" in log
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
                state=TaskState.completed,
                message=status_msg,
            ),
            history=history,
            artifacts=artifacts,
            metadata=metadata,
        )
        resp_data = {"id": "resp-1", "jsonrpc": "2.0", "result": task.model_dump(by_alias=True, exclude_none=True)}
        return SendMessageResponse.model_validate(resp_data)

    def _make_message_response(self, *, parts=None, metadata=None):
        msg = Message(
            message_id="m1",
            role=Role.agent,
            parts=parts or [Part(root=TextPart(text="answer"))],
            metadata=metadata,
        )
        resp_data = {"id": "resp-1", "jsonrpc": "2.0", "result": msg.model_dump(by_alias=True, exclude_none=True)}
        return SendMessageResponse.model_validate(resp_data)

    def _make_error_response(self):
        resp_data = {
            "id": "resp-1",
            "jsonrpc": "2.0",
            "error": {
                "code": -32600,
                "message": "Invalid request",
            },
        }
        return SendMessageResponse.model_validate(resp_data)

    def test_error_response(self):
        resp = self._make_error_response()
        log = build_a2a_response_log(resp)
        assert "Type: ERROR" in log
        assert "Invalid request" in log

    def test_task_response_basic(self):
        resp = self._make_task_response()
        log = build_a2a_response_log(resp)
        assert "Type: SUCCESS" in log
        assert "Task" in log
        assert "completed" in log

    def test_task_response_with_status_message(self):
        status_msg = Message(
            message_id="sm-1",
            role=Role.agent,
            parts=[Part(root=TextPart(text="done"))],
        )
        resp = self._make_task_response(status_msg=status_msg)
        log = build_a2a_response_log(resp)
        assert "sm-1" in log

    def test_task_response_with_history(self):
        history = [
            Message(message_id="h1", role=Role.user, parts=[Part(root=TextPart(text="q"))]),
            Message(message_id="h2", role=Role.agent, parts=[Part(root=TextPart(text="a"))]),
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
