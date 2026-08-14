# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a.converters._event_converter."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import (
    Artifact,
    Message,
    Part as A2APart,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.genai import types as genai_types
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.server.a2a._constants import (
    A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY,
    A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL,
    A2A_DATA_PART_METADATA_TYPE_KEY,
    A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA,
    DEFAULT_ERROR_MESSAGE,
    INTERACTION_SPEC_VERSION,
    MESSAGE_METADATA_INTERACTION_SPEC_VERSION_KEY,
    MESSAGE_METADATA_OBJECT_TYPE_KEY,
    MESSAGE_METADATA_RESPONSE_ID_KEY,
    MESSAGE_METADATA_TAG_KEY,
    REQUEST_EUC_FUNCTION_CALL_NAME,
)
from trpc_agent_sdk.server.a2a.converters._event_converter import (
    _build_context_metadata,
    _build_event_metadata,
    _build_message,
    _build_message_metadata,
    _collect_parts,
    _create_artifact_update_event,
    _create_error_status_event,
    _create_status_update_event,
    _default_object_type,
    _get_event_type,
    _infer_a2a_message_object_type,
    _infer_message_object_type,
    _infer_message_tag,
    _is_streaming_delta,
    _mark_long_running_tools,
    build_request_message_metadata,
    convert_a2a_message_to_event,
    convert_a2a_task_to_event,
    convert_content_to_a2a_message,
    convert_event_to_a2a_events,
    convert_event_to_a2a_message,
    create_cancellation_event,
    create_completed_status_event,
    create_exception_status_event,
    create_final_status_event,
    create_submitted_status_event,
    create_working_status_event,
)
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part


def _make_invocation_context(**overrides):
    ctx = MagicMock(spec=InvocationContext)
    ctx.invocation_id = overrides.get("invocation_id", "inv-1")
    ctx.user_id = overrides.get("user_id", "user-1")
    ctx.app_name = overrides.get("app_name", "test-app")
    ctx.branch = overrides.get("branch", None)
    session = MagicMock()
    session.id = overrides.get("session_id", "session-1")
    ctx.session = session
    return ctx


def _make_event(*, text=None, function_call=None, function_response=None,
                executable_code=None, code_execution_result=None,
                partial=None, author="agent", object_type=None, tag=None,
                error_code=None, error_message=None, long_running_tool_ids=None,
                response_id=None):
    parts = []
    if text:
        parts.append(Part(text=text))
    if function_call:
        parts.append(Part(function_call=function_call))
    if function_response:
        parts.append(Part(function_response=function_response))
    if executable_code:
        parts.append(Part(executable_code=executable_code))
    if code_execution_result:
        parts.append(Part(code_execution_result=code_execution_result))
    content = Content(role="model", parts=parts) if parts else None
    return Event(
        invocation_id="inv-1",
        author=author,
        content=content,
        partial=partial,
        object=object_type,
        tag=tag,
        error_code=error_code,
        error_message=error_message,
        long_running_tool_ids=long_running_tool_ids,
        response_id=response_id,
    )


def _data_part(data: dict, metadata: dict | None = None) -> A2APart:
    """Build a Part with a structured ``data`` field."""
    return A2APart(
        data=ParseDict(data, struct_pb2.Value()),
        metadata=metadata,
    )


def _meta_dict(message) -> dict:
    return MessageToDict(message.metadata)


# ---------------------------------------------------------------------------
# build_request_message_metadata
# ---------------------------------------------------------------------------
class TestBuildRequestMessageMetadata:
    def test_includes_version(self):
        ctx = _make_invocation_context()
        meta = build_request_message_metadata(ctx)
        assert meta[MESSAGE_METADATA_INTERACTION_SPEC_VERSION_KEY] == INTERACTION_SPEC_VERSION

    def test_includes_invocation_id(self):
        ctx = _make_invocation_context(invocation_id="inv-42")
        meta = build_request_message_metadata(ctx)
        assert meta["invocation_id"] == "inv-42"

    def test_includes_user_id(self):
        ctx = _make_invocation_context(user_id="u1")
        meta = build_request_message_metadata(ctx)
        assert meta["user_id"] == "u1"

    def test_missing_invocation_id(self):
        ctx = _make_invocation_context()
        ctx.invocation_id = None
        meta = build_request_message_metadata(ctx)
        assert "invocation_id" not in meta

    def test_missing_user_id(self):
        ctx = _make_invocation_context()
        ctx.user_id = None
        meta = build_request_message_metadata(ctx)
        assert "user_id" not in meta


# ---------------------------------------------------------------------------
# _infer_message_object_type
# ---------------------------------------------------------------------------
class TestInferMessageObjectType:
    def test_uses_event_object_if_set(self):
        event = _make_event(text="hi", object_type="custom.type")
        assert _infer_message_object_type(event) == "custom.type"

    def test_function_response(self):
        event = _make_event(function_response=FunctionResponse(name="fn", response={"r": 1}))
        assert _infer_message_object_type(event) == "tool.response"

    def test_function_call(self):
        event = _make_event(function_call=FunctionCall(name="fn", args={}))
        assert _infer_message_object_type(event) == "chat.completion"

    def test_text_partial(self):
        event = _make_event(text="hi", partial=True)
        assert _infer_message_object_type(event) == "chat.completion.chunk"

    def test_text_non_partial(self):
        event = _make_event(text="hi", partial=False)
        assert _infer_message_object_type(event) == "chat.completion"

    def test_no_content(self):
        event = _make_event()
        assert _infer_message_object_type(event) is None


# ---------------------------------------------------------------------------
# _infer_a2a_message_object_type
# ---------------------------------------------------------------------------
class TestInferA2aMessageObjectType:
    def test_function_response(self):
        parts = [genai_types.Part(function_response=genai_types.FunctionResponse(name="fn", response={}))]
        assert _infer_a2a_message_object_type(parts) == "tool.response"

    def test_function_call(self):
        parts = [genai_types.Part(function_call=genai_types.FunctionCall(name="fn", args={}))]
        assert _infer_a2a_message_object_type(parts) == "chat.completion"

    def test_text_partial(self):
        parts = [genai_types.Part(text="hi")]
        assert _infer_a2a_message_object_type(parts, partial=True) == "chat.completion.chunk"

    def test_text_non_partial(self):
        parts = [genai_types.Part(text="hi")]
        assert _infer_a2a_message_object_type(parts, partial=False) == "chat.completion"

    def test_empty(self):
        assert _infer_a2a_message_object_type([]) is None


# ---------------------------------------------------------------------------
# _default_object_type
# ---------------------------------------------------------------------------
class TestDefaultObjectType:
    def test_partial(self):
        assert _default_object_type(True) == "chat.completion.chunk"

    def test_non_partial(self):
        assert _default_object_type(False) == "chat.completion"


# ---------------------------------------------------------------------------
# _infer_message_tag
# ---------------------------------------------------------------------------
class TestInferMessageTag:
    def test_uses_event_tag(self):
        event = _make_event(text="hi", tag="custom")
        assert _infer_message_tag(event) == "custom"

    def test_code_execution_result(self):
        event = _make_event(code_execution_result=genai_types.CodeExecutionResult(output="x", outcome="OUTCOME_OK"))
        assert _infer_message_tag(event) == "code_execution_result"

    def test_executable_code(self):
        event = _make_event(executable_code=genai_types.ExecutableCode(code="x", language="PYTHON"))
        assert _infer_message_tag(event) == "code_execution_code"

    def test_no_content(self):
        event = _make_event()
        assert _infer_message_tag(event) == ""

    def test_text_no_tag(self):
        event = _make_event(text="hi")
        assert _infer_message_tag(event) == ""


# ---------------------------------------------------------------------------
# _get_event_type
# ---------------------------------------------------------------------------
class TestGetEventType:
    def test_no_content(self):
        event = _make_event()
        assert _get_event_type(event) is None

    def test_text(self):
        event = _make_event(text="hi")
        assert _get_event_type(event) == "text"

    def test_function_call_via_object(self):
        event = _make_event(function_call=FunctionCall(name="fn", args={}), object_type="chat.completion")
        assert _get_event_type(event) == "tool_call"

    def test_function_response_via_object(self):
        event = _make_event(function_response=FunctionResponse(name="fn", response={}), object_type="tool.response")
        assert _get_event_type(event) == "tool_response"

    def test_function_response_without_object(self):
        event = _make_event(function_response=FunctionResponse(name="fn", response={}))
        assert _get_event_type(event) == "tool_response"

    def test_function_call_without_object(self):
        event = _make_event(function_call=FunctionCall(name="fn", args={}))
        assert _get_event_type(event) == "tool_call"

    def test_code_execution_result_tag(self):
        event = _make_event(
            code_execution_result=genai_types.CodeExecutionResult(output="x", outcome="OUTCOME_OK"),
            tag="code_execution_result",
        )
        assert _get_event_type(event) == "code_execution_result"


# ---------------------------------------------------------------------------
# _build_context_metadata
# ---------------------------------------------------------------------------
class TestBuildContextMetadata:
    def test_includes_required_fields(self):
        ctx = _make_invocation_context()
        event = _make_event(text="hi")
        meta = _build_context_metadata(event, ctx)
        assert meta["app_name"] == "test-app"
        assert meta["user_id"] == "user-1"
        assert meta["session_id"] == "session-1"
        assert meta["author"] == "agent"

    def test_includes_optional_fields(self):
        ctx = _make_invocation_context(branch="b1")
        event = _make_event(text="hi", partial=True)
        event.branch = "b1"
        meta = _build_context_metadata(event, ctx)
        assert "branch" in meta
        assert meta["partial"] == "True"


# ---------------------------------------------------------------------------
# _build_message_metadata
# ---------------------------------------------------------------------------
class TestBuildMessageMetadata:
    def test_includes_object_type_and_tag(self):
        event = _make_event(text="hi", object_type="chat.completion", tag="my_tag")
        meta = _build_message_metadata(event, "eff-1")
        assert meta[MESSAGE_METADATA_OBJECT_TYPE_KEY] == "chat.completion"
        assert meta[MESSAGE_METADATA_TAG_KEY] == "my_tag"
        assert meta[MESSAGE_METADATA_RESPONSE_ID_KEY] == "eff-1"


# ---------------------------------------------------------------------------
# _mark_long_running_tools
# ---------------------------------------------------------------------------
class TestMarkLongRunningTools:
    def test_marks_matching_tool_ids(self):
        a2a_part = _data_part(
            {"id": "tool1", "name": "fn"},
            {A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL},
        )
        event = _make_event(function_call=FunctionCall(name="fn", args={}), long_running_tool_ids={"tool1"})
        _mark_long_running_tools([a2a_part], event)
        assert MessageToDict(a2a_part.metadata)[A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY] is True

    def test_does_nothing_without_long_running_ids(self):
        a2a_part = _data_part(
            {"id": "tool1"},
            {A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL},
        )
        event = _make_event(function_call=FunctionCall(name="fn", args={}))
        _mark_long_running_tools([a2a_part], event)
        assert A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY not in MessageToDict(a2a_part.metadata)


# ---------------------------------------------------------------------------
# _build_message
# ---------------------------------------------------------------------------
class TestBuildMessage:
    def test_returns_none_for_empty_parts(self):
        event = _make_event(text="hi")
        assert _build_message(event, [], Role.ROLE_AGENT, "e1") is None

    def test_returns_message_with_parts(self):
        event = _make_event(text="hi", response_id="resp-1")
        parts = [A2APart(text="hi")]
        msg = _build_message(event, parts, Role.ROLE_AGENT, "resp-1")
        assert msg is not None
        assert msg.role == Role.ROLE_AGENT
        assert msg.message_id == "resp-1"
        assert len(msg.parts) == 1


# ---------------------------------------------------------------------------
# _is_streaming_delta
# ---------------------------------------------------------------------------
class TestIsStreamingDelta:
    def test_true(self):
        part = _data_part(
            {},
            {A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA},
        )
        assert _is_streaming_delta(part) is True

    def test_false(self):
        part = _data_part(
            {},
            {A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL},
        )
        assert _is_streaming_delta(part) is False


# ---------------------------------------------------------------------------
# convert_event_to_a2a_message
# ---------------------------------------------------------------------------
class TestConvertEventToA2aMessage:
    def test_text_event(self):
        event = _make_event(text="hello")
        ctx = _make_invocation_context()
        msg = convert_event_to_a2a_message(event, ctx)
        assert msg is not None
        assert len(msg.parts) == 1

    def test_none_event_raises(self):
        ctx = _make_invocation_context()
        with pytest.raises(ValueError, match="Event cannot be None"):
            convert_event_to_a2a_message(None, ctx)

    def test_none_context_raises(self):
        event = _make_event(text="hi")
        with pytest.raises(ValueError, match="Invocation context cannot be None"):
            convert_event_to_a2a_message(event, None)

    def test_empty_content_returns_none(self):
        event = _make_event()
        ctx = _make_invocation_context()
        assert convert_event_to_a2a_message(event, ctx) is None

    def test_function_call_event(self):
        event = _make_event(function_call=FunctionCall(name="fn", args={"x": 1}))
        ctx = _make_invocation_context()
        msg = convert_event_to_a2a_message(event, ctx)
        assert msg is not None


# ---------------------------------------------------------------------------
# convert_content_to_a2a_message
# ---------------------------------------------------------------------------
class TestConvertContentToA2aMessage:
    def test_basic(self):
        content = genai_types.Content(role="user", parts=[genai_types.Part(text="hi")])
        msg = convert_content_to_a2a_message([content])
        assert msg is not None
        assert msg.role == Role.ROLE_AGENT

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Contents cannot be None or empty"):
            convert_content_to_a2a_message([])

    def test_none_raises(self):
        with pytest.raises(ValueError, match="Contents cannot be None or empty"):
            convert_content_to_a2a_message(None)

    def test_empty_parts_returns_none(self):
        content = genai_types.Content(role="user", parts=[])
        result = convert_content_to_a2a_message([content])
        assert result is None

    def test_custom_role(self):
        content = genai_types.Content(role="user", parts=[genai_types.Part(text="hi")])
        msg = convert_content_to_a2a_message([content], role=Role.ROLE_USER)
        assert msg.role == Role.ROLE_USER


# ---------------------------------------------------------------------------
# convert_a2a_task_to_event
# ---------------------------------------------------------------------------
class TestConvertA2aTaskToEvent:
    def test_none_raises(self):
        with pytest.raises(ValueError, match="A2A task cannot be None"):
            convert_a2a_task_to_event(None)

    def test_task_with_artifacts(self):
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[
                Artifact(
                    artifact_id="a1",
                    parts=[A2APart(text="result")],
                )
            ],
        )
        event = convert_a2a_task_to_event(task, author="bot")
        assert event.author == "bot"
        assert event.content is not None

    def test_task_with_status_message(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="status")],
        )
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )
        event = convert_a2a_task_to_event(task)
        assert event.content is not None

    def test_task_with_history(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="history")],
        )
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            history=[msg],
        )
        event = convert_a2a_task_to_event(task)
        assert event.content is not None

    def test_task_without_message(self):
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        ctx = _make_invocation_context()
        event = convert_a2a_task_to_event(task, invocation_context=ctx)
        assert event.invocation_id == "inv-1"


# ---------------------------------------------------------------------------
# convert_a2a_message_to_event
# ---------------------------------------------------------------------------
class TestConvertA2aMessageToEvent:
    def test_none_raises(self):
        with pytest.raises(ValueError, match="A2A message cannot be None"):
            convert_a2a_message_to_event(None)

    def test_basic_text(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="hello")],
        )
        event = convert_a2a_message_to_event(msg, author="bot")
        assert event.author == "bot"
        assert event.content.parts[0].text == "hello"

    def test_empty_parts(self):
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[])
        event = convert_a2a_message_to_event(msg, author="bot")
        assert event.content is not None

    def test_partial_flag(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="hi")],
        )
        event = convert_a2a_message_to_event(msg, partial=True)
        assert event.partial is True

    def test_with_invocation_context(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="hi")],
        )
        ctx = _make_invocation_context(invocation_id="inv-99", branch="b1")
        event = convert_a2a_message_to_event(msg, invocation_context=ctx)
        assert event.invocation_id == "inv-99"
        assert event.branch == "b1"

    def test_long_running_tool_detected(self):
        dp = _data_part(
            {"name": "fn", "id": "tool1", "args": "{}"},
            {
                A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL,
                A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY: True,
            },
        )
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[dp],
        )
        event = convert_a2a_message_to_event(msg)
        assert event.long_running_tool_ids is not None

    def test_metadata_object_type_used(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="hi")],
            metadata={MESSAGE_METADATA_OBJECT_TYPE_KEY: "custom.type"},
        )
        event = convert_a2a_message_to_event(msg)
        assert event.object == "custom.type"


# ---------------------------------------------------------------------------
# create_* status event factories
# ---------------------------------------------------------------------------
class TestCreateStatusEvents:
    def test_cancellation_event(self):
        evt = create_cancellation_event("t1", "ctx1", "cancelled")
        assert evt.status.state == TaskState.TASK_STATE_CANCELED
        assert evt.task_id == "t1"

    def test_exception_status_event(self):
        evt = create_exception_status_event("t1", "ctx1", "error occurred")
        assert evt.status.state == TaskState.TASK_STATE_FAILED

    def test_submitted_status_event(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[])
        evt = create_submitted_status_event("t1", "ctx1", msg)
        assert evt.status.state == TaskState.TASK_STATE_SUBMITTED

    def test_working_status_event(self):
        evt = create_working_status_event("t1", "ctx1")
        assert evt.status.state == TaskState.TASK_STATE_WORKING

    def test_working_status_event_with_metadata(self):
        evt = create_working_status_event("t1", "ctx1", metadata={"k": "v"})
        assert _meta_dict(evt) == {"k": "v"}

    def test_completed_status_event(self):
        evt = create_completed_status_event("t1", "ctx1")
        assert evt.status.state == TaskState.TASK_STATE_COMPLETED

    def test_final_status_event(self):
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[])
        evt = create_final_status_event("t1", "ctx1", TaskState.TASK_STATE_INPUT_REQUIRED, message=msg)
        assert evt.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert evt.status.message == msg


# ---------------------------------------------------------------------------
# _create_error_status_event
# ---------------------------------------------------------------------------
class TestCreateErrorStatusEvent:
    def test_basic_error(self):
        event = _make_event(text="hi", error_code="500", error_message="Server error")
        ctx = _make_invocation_context()
        result = _create_error_status_event(event, ctx, "t1", "ctx1")
        assert result.status.state == TaskState.TASK_STATE_FAILED
        assert "Server error" in result.status.message.parts[0].text

    def test_default_error_message(self):
        event = _make_event(error_code="500")
        ctx = _make_invocation_context()
        result = _create_error_status_event(event, ctx, "t1", "ctx1")
        assert DEFAULT_ERROR_MESSAGE in result.status.message.parts[0].text


# ---------------------------------------------------------------------------
# _create_status_update_event
# ---------------------------------------------------------------------------
class TestCreateStatusUpdateEvent:
    def test_basic_working(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="hi")],
        )
        event = _make_event(text="hi")
        ctx = _make_invocation_context()
        result = _create_status_update_event(msg, ctx, event, "t1", "ctx1", effective_id="m1")
        assert result.status.state == TaskState.TASK_STATE_WORKING

    def test_auth_required_for_euc(self):
        dp = _data_part(
            {"id": "t1", "name": REQUEST_EUC_FUNCTION_CALL_NAME},
            {
                A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL,
                A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY: True,
            },
        )
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[dp])
        event = _make_event(function_call=FunctionCall(name=REQUEST_EUC_FUNCTION_CALL_NAME, args={}))
        ctx = _make_invocation_context()
        result = _create_status_update_event(msg, ctx, event, "t1", "ctx1", effective_id="m1")
        assert result.status.state == TaskState.TASK_STATE_AUTH_REQUIRED

    def test_input_required_for_long_running(self):
        dp = _data_part(
            {"id": "t1", "name": "other_tool"},
            {
                A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL,
                A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY: True,
            },
        )
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[dp])
        event = _make_event(function_call=FunctionCall(name="other_tool", args={}))
        ctx = _make_invocation_context()
        result = _create_status_update_event(msg, ctx, event, "t1", "ctx1", effective_id="m1")
        assert result.status.state == TaskState.TASK_STATE_INPUT_REQUIRED


# ---------------------------------------------------------------------------
# _create_artifact_update_event
# ---------------------------------------------------------------------------
class TestCreateArtifactUpdateEvent:
    def test_basic(self):
        msg = Message(
            message_id="m1",
            role=Role.ROLE_AGENT,
            parts=[A2APart(text="hi")],
        )
        event = _make_event(text="hi", response_id="resp-1")
        ctx = _make_invocation_context()
        result = _create_artifact_update_event(
            msg, event, ctx, task_id="t1", context_id="ctx1", effective_id="m1"
        )
        assert result.artifact.artifact_id == "m1"
        assert result.last_chunk is False

    def test_last_chunk(self):
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[A2APart(text="hi")])
        event = _make_event(text="hi")
        ctx = _make_invocation_context()
        result = _create_artifact_update_event(
            msg, event, ctx, task_id="t1", context_id="ctx1", last_chunk=True, effective_id="m1"
        )
        assert result.last_chunk is True
        assert result.artifact.artifact_id == ""
        assert len(result.artifact.parts) == 0


# ---------------------------------------------------------------------------
# convert_event_to_a2a_events
# ---------------------------------------------------------------------------
class TestConvertEventToA2aEvents:
    def test_none_event_raises(self):
        ctx = _make_invocation_context()
        with pytest.raises(ValueError, match="Event cannot be None"):
            convert_event_to_a2a_events(None, ctx)

    def test_none_context_raises(self):
        event = _make_event(text="hi")
        with pytest.raises(ValueError, match="Invocation context cannot be None"):
            convert_event_to_a2a_events(event, None)

    def test_text_event_produces_artifact(self):
        event = _make_event(text="hello", partial=True)
        ctx = _make_invocation_context()
        events = convert_event_to_a2a_events(event, ctx, task_id="t1", context_id="ctx1")
        assert len(events) >= 1
        has_artifact = any(isinstance(e, TaskArtifactUpdateEvent) for e in events)
        assert has_artifact

    def test_error_event_produces_status_update(self):
        event = _make_event(text="hi", error_code="500", error_message="fail")
        ctx = _make_invocation_context()
        events = convert_event_to_a2a_events(event, ctx, task_id="t1", context_id="ctx1")
        # a2a-sdk 1.x forbids a bare Message in task mode; the failure is carried
        # through the failed TaskStatusUpdateEvent.
        has_status = any(
            isinstance(e, TaskStatusUpdateEvent)
            and e.status.state == TaskState.TASK_STATE_FAILED
            and e.status.HasField("message")
            for e in events
        )
        assert has_status
        assert not any(isinstance(e, Message) for e in events)

    def test_on_event_callback_called(self):
        event = _make_event(text="hello", partial=True)
        ctx = _make_invocation_context()
        callback_events = []
        convert_event_to_a2a_events(event, ctx, task_id="t1", context_id="ctx1", on_event=callback_events.append)
        assert len(callback_events) > 0

    def test_empty_content_no_artifacts(self):
        event = _make_event()
        ctx = _make_invocation_context()
        events = convert_event_to_a2a_events(event, ctx)
        assert len(events) == 0
