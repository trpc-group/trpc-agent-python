# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Event conversion between TrpcAgent events and A2A events.

Metadata uses unprefixed keys.  Streaming uses the artifact-first flow
(``TaskArtifactUpdateEvent``).
"""

from __future__ import annotations

_TYPE_TOOL_CALL = "tool_call"
_TYPE_TOOL_RESPONSE = "tool_response"
_TYPE_CODE_EXECUTION = "code_execution"
_TYPE_CODE_EXECUTION_RESULT = "code_execution_result"
_TYPE_STREAMING_TOOL_CALL = "streaming_tool_call"
_TYPE_TEXT = "text"

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
import uuid

from a2a.server.events import Event as A2AEvent
from a2a.types import (
    Artifact,
    DataPart,
    Message,
    Part as A2APart,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from google.genai import types as genai_types

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger

from .._constants import INTERACTION_SPEC_VERSION
from .._constants import MESSAGE_METADATA_INTERACTION_SPEC_VERSION_KEY
from .._constants import MESSAGE_METADATA_OBJECT_TYPE_KEY
from .._constants import MESSAGE_METADATA_RESPONSE_ID_KEY
from .._constants import MESSAGE_METADATA_TAG_KEY
from .._constants import REQUEST_EUC_FUNCTION_CALL_NAME
from .._constants import A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY
from .._constants import A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL
from .._constants import A2A_DATA_PART_METADATA_TYPE_KEY
from .._constants import A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA
from .._constants import DEFAULT_ERROR_MESSAGE
from .._utils import get_metadata
from .._utils import metadata_is_true
from .._utils import set_metadata
from ._part_converter import convert_a2a_part_to_genai_part
from ._part_converter import convert_genai_part_to_a2a_part


def build_request_message_metadata(invocation_context: InvocationContext) -> Dict[str, Any]:
    """Build ``Message.metadata`` for an outgoing A2A request."""
    metadata: Dict[str, Any] = {
        MESSAGE_METADATA_INTERACTION_SPEC_VERSION_KEY: INTERACTION_SPEC_VERSION,
    }
    if invocation_context.invocation_id:
        metadata["invocation_id"] = invocation_context.invocation_id
    if invocation_context.user_id:
        metadata["user_id"] = invocation_context.user_id
    return metadata


def _serialize_metadata_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(exclude_none=True, by_alias=True)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to serialize metadata value: %s", ex)
            return str(value)
    return str(value)


def _infer_message_object_type(event: Event) -> Optional[str]:
    if event.object:
        return event.object
    if not event.content or not event.content.parts:
        return None
    parts = event.content.parts
    if any(p.function_response for p in parts):
        return "tool.response"
    if any(p.code_execution_result or p.executable_code for p in parts):
        return "postprocessing.code_execution"
    if any(p.function_call for p in parts):
        return "chat.completion"
    return "chat.completion.chunk" if event.partial else "chat.completion"


def _infer_a2a_message_object_type(parts: List[genai_types.Part], partial: bool = False) -> Optional[str]:
    if any(p.function_response for p in parts):
        return "tool.response"
    if any(p.code_execution_result or p.executable_code for p in parts):
        return "postprocessing.code_execution"
    if any(p.function_call for p in parts):
        return "chat.completion"
    if any(p.text for p in parts):
        return "chat.completion.chunk" if partial else "chat.completion"
    return None


def _default_object_type(partial: bool = False) -> str:
    return "chat.completion.chunk" if partial else "chat.completion"


def _infer_message_tag(event: Event) -> str:
    if event.tag:
        return event.tag
    if not event.content or not event.content.parts:
        return ""
    if any(p.code_execution_result for p in event.content.parts):
        return "code_execution_result"
    if any(p.executable_code for p in event.content.parts):
        return "code_execution_code"
    return ""


def _get_event_type(event: Event) -> Optional[str]:
    """Determine event type for conversion; streaming first, then object/tag, then content."""
    if not event.content or not event.content.parts:
        return None

    if event.is_streaming_tool_call():
        return _TYPE_STREAMING_TOOL_CALL

    parts = event.content.parts

    if event.object:
        if event.object == "tool.response":
            return _TYPE_TOOL_RESPONSE
        if event.object == "postprocessing.code_execution":
            if any(p.code_execution_result for p in parts):
                return _TYPE_CODE_EXECUTION_RESULT
            return _TYPE_CODE_EXECUTION
        if event.object in ("chat.completion", "chat.completion.chunk"):
            if any(p.function_call for p in parts):
                return _TYPE_TOOL_CALL
            return _TYPE_TEXT

    if event.tag == "code_execution_result":
        return _TYPE_CODE_EXECUTION_RESULT
    if event.tag == "code_execution_code":
        return _TYPE_CODE_EXECUTION

    if any(p.function_response for p in parts):
        return _TYPE_TOOL_RESPONSE
    if any(p.code_execution_result for p in parts):
        return _TYPE_CODE_EXECUTION_RESULT
    if any(p.executable_code for p in parts):
        return _TYPE_CODE_EXECUTION
    if any(p.function_call for p in parts):
        return _TYPE_TOOL_CALL
    if any(p.text for p in parts):
        return _TYPE_TEXT

    return None


def _build_context_metadata(event: Event, ctx: InvocationContext) -> Dict[str, Any]:
    """Build per-event metadata carrying context information."""
    metadata: Dict[str, Any] = {
        "app_name": ctx.app_name,
        "user_id": ctx.user_id,
        "session_id": ctx.session.id,
        "invocation_id": event.invocation_id,
        "author": event.author,
    }
    partial = event.partial or False
    optional_fields = [
        ("branch", event.branch),
        ("grounding_metadata", event.grounding_metadata),
        ("custom_metadata", event.custom_metadata),
        ("usage_metadata", event.usage_metadata),
        ("error_code", event.error_code),
        ("partial", partial),
    ]
    for name, value in optional_fields:
        if value is not None:
            metadata[name] = _serialize_metadata_value(value)
    return metadata


def _build_message_metadata(event: Event, effective_id: str) -> Dict[str, Any]:
    """Build message/event metadata (object_type, tag, llm_response_id)."""
    return {
        MESSAGE_METADATA_OBJECT_TYPE_KEY: _infer_message_object_type(event) or "",
        MESSAGE_METADATA_TAG_KEY: _infer_message_tag(event),
        MESSAGE_METADATA_RESPONSE_ID_KEY: effective_id,
    }


def _build_event_metadata(event: Event, message: Message, ctx: InvocationContext, effective_id: str) -> Dict[str, Any]:
    metadata = _build_context_metadata(event, ctx)
    msg_meta = _build_message_metadata(event, effective_id)
    set_metadata(metadata, MESSAGE_METADATA_OBJECT_TYPE_KEY, msg_meta.get(MESSAGE_METADATA_OBJECT_TYPE_KEY) or "")
    set_metadata(metadata, MESSAGE_METADATA_TAG_KEY, msg_meta.get(MESSAGE_METADATA_TAG_KEY) or "")
    set_metadata(metadata, MESSAGE_METADATA_RESPONSE_ID_KEY, msg_meta.get(MESSAGE_METADATA_RESPONSE_ID_KEY) or "")
    if any(
            get_metadata(p.root.metadata, A2A_DATA_PART_METADATA_TYPE_KEY) ==
            A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA for p in message.parts if p.root.metadata):
        set_metadata(metadata, "streaming_tool_call", "true")
    return metadata


def _mark_long_running_tools(a2a_parts: List[A2APart], event: Event) -> None:
    """Annotate parts that correspond to long-running tool calls."""
    if not event.long_running_tool_ids:
        return
    for a2a_part in a2a_parts:
        root = a2a_part.root
        if (isinstance(root, DataPart) and root.metadata and get_metadata(
                root.metadata, A2A_DATA_PART_METADATA_TYPE_KEY) == A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL
                and root.data.get("id") in event.long_running_tool_ids):
            set_metadata(root.metadata, A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY, True)


def _effective_response_id(event: Event) -> str:
    """Return ``response_id`` when present, otherwise a new UUID.

    Callers that need the same id across multiple locations should invoke this
    once and pass the result explicitly.
    """
    return event.response_id or str(uuid.uuid4())


def _build_message(event: Event, a2a_parts: List[A2APart], role: Role, effective_id: str) -> Optional[Message]:
    """Assemble an A2A Message from converted parts, or return None if empty."""
    if not a2a_parts:
        return None
    message = Message(message_id=effective_id, role=role, parts=a2a_parts)
    msg_meta = _build_message_metadata(event, effective_id)
    if msg_meta:
        message.metadata = msg_meta
    return message


def _is_streaming_delta(a2a_part: A2APart) -> bool:
    return (a2a_part.root.metadata is not None and get_metadata(a2a_part.root.metadata, A2A_DATA_PART_METADATA_TYPE_KEY)
            == A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA)


def _collect_parts(
    event: Event,
    *,
    accept: Optional[callable] = None,
    post: Optional[callable] = None,
) -> List[A2APart]:
    """Convert genai parts to A2A parts with optional per-type filtering and post-processing.

    Args:
        event: Source event whose content.parts will be converted.
        accept: If provided, only A2A parts for which ``accept(part)`` is True are kept.
        post: If provided, called on the collected list after filtering (e.g. to annotate).
    """
    a2a_parts: List[A2APart] = []
    for gpart in event.content.parts:
        a2a_part = convert_genai_part_to_a2a_part(gpart)
        if a2a_part is None:
            continue
        if accept is not None and not accept(a2a_part):
            continue
        a2a_parts.append(a2a_part)
    if post is not None:
        post(a2a_parts, event)
    return a2a_parts


_EVENT_TYPE_PART_RULES: dict[str, dict] = {
    _TYPE_TOOL_CALL: {
        "post": _mark_long_running_tools
    },
    _TYPE_STREAMING_TOOL_CALL: {
        "accept": _is_streaming_delta
    },
    _TYPE_TOOL_RESPONSE: {},
    _TYPE_CODE_EXECUTION: {},
    _TYPE_CODE_EXECUTION_RESULT: {},
    _TYPE_TEXT: {},
}


def convert_event_to_a2a_message(
    event: Event,
    invocation_context: InvocationContext,
    role: Role = Role.agent,
) -> Optional[Message]:
    """Convert a TrpcAgent Event to an A2A Message.

    Each event type defines its own part-selection and post-processing rules
    via ``_EVENT_TYPE_PART_RULES``.  Returns None when the event has no content.
    """
    if not event:
        raise ValueError("Event cannot be None")
    if not invocation_context:
        raise ValueError("Invocation context cannot be None")
    if not event.content or not event.content.parts:
        return None

    event_type = _get_event_type(event)
    rules = _EVENT_TYPE_PART_RULES.get(event_type) if event_type else None
    if rules is None:
        return None

    a2a_parts = _collect_parts(event, **rules)
    effective_id = _effective_response_id(event)
    return _build_message(event, a2a_parts, role, effective_id)


def convert_content_to_a2a_message(
    contents: List[genai_types.Content],
    role: Role = Role.agent,
) -> Optional[Message]:
    """Convert a list of Content objects to a single A2A Message.

    Raises:
        ValueError: If *contents* is None or empty.
    """
    if not contents:
        raise ValueError("Contents cannot be None or empty")

    a2a_parts: List[A2APart] = []
    for content in contents:
        if not content or not content.parts:
            continue
        for part in content.parts:
            a2a_part = convert_genai_part_to_a2a_part(part)
            if a2a_part:
                a2a_parts.append(a2a_part)

    if a2a_parts:
        return Message(message_id=str(uuid.uuid4()), role=role, parts=a2a_parts)
    return None


def convert_a2a_task_to_event(
    a2a_task: Task,
    author: Optional[str] = None,
    invocation_context: Optional[InvocationContext] = None,
) -> Event:
    """Convert an A2A Task to a TrpcAgent Event.

    Raises:
        ValueError: If *a2a_task* is None.
    """
    if a2a_task is None:
        raise ValueError("A2A task cannot be None")

    message = None
    if a2a_task.artifacts:
        message = Message(
            message_id="",
            role=Role.agent,
            parts=a2a_task.artifacts[-1].parts,
            metadata=getattr(a2a_task.artifacts[-1], "metadata", None),
        )
    elif a2a_task.status and a2a_task.status.message:
        message = a2a_task.status.message
    elif a2a_task.history:
        message = a2a_task.history[-1]

    if message:
        return convert_a2a_message_to_event(message, author, invocation_context)

    return Event(
        invocation_id=(invocation_context.invocation_id if invocation_context else str(uuid.uuid4())),
        author=author or "a2a agent",
        branch=invocation_context.branch if invocation_context else None,
    )


def convert_a2a_message_to_event(
    a2a_message: Message,
    author: Optional[str] = None,
    invocation_context: Optional[InvocationContext] = None,
    partial: bool = False,
) -> Event:
    """Convert an A2A Message to a TrpcAgent Event.

    Raises:
        ValueError: If *a2a_message* is None.
    """
    if a2a_message is None:
        raise ValueError("A2A message cannot be None")

    inv_id = invocation_context.invocation_id if invocation_context else str(uuid.uuid4())
    branch = invocation_context.branch if invocation_context else None
    msg_meta = getattr(a2a_message, "metadata", None)

    if not a2a_message.parts:
        logger.warning("A2A message has no parts, creating event with empty content")
        return Event(
            invocation_id=inv_id,
            author=author or "a2a agent",
            branch=branch,
            content=genai_types.Content(role="model", parts=[]),
            object=get_metadata(msg_meta, MESSAGE_METADATA_OBJECT_TYPE_KEY) or _default_object_type(partial),
            tag=get_metadata(msg_meta, MESSAGE_METADATA_TAG_KEY),
            response_id=get_metadata(msg_meta, MESSAGE_METADATA_RESPONSE_ID_KEY) or a2a_message.message_id,
            partial=partial,
        )

    parts: List[genai_types.Part] = []
    long_running_tool_ids: set[str] = set()

    for a2a_part in a2a_message.parts:
        try:
            gpart = convert_a2a_part_to_genai_part(a2a_part)
            if gpart is None:
                logger.warning("Failed to convert A2A part, skipping: %s", a2a_part)
                continue
            if (metadata_is_true(a2a_part.root.metadata, A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY)
                    and gpart.function_call):
                long_running_tool_ids.add(gpart.function_call.id)
            parts.append(gpart)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to convert A2A part: %s, error: %s", a2a_part, ex)
            continue

    if not parts:
        logger.warning("No parts could be converted from A2A message %s", a2a_message)

    object_type = (get_metadata(msg_meta, MESSAGE_METADATA_OBJECT_TYPE_KEY)
                   or _infer_a2a_message_object_type(parts, partial=partial) or _default_object_type(partial))

    return Event(
        invocation_id=inv_id,
        author=author or "a2a agent",
        branch=branch,
        long_running_tool_ids=long_running_tool_ids or None,
        object=object_type,
        tag=get_metadata(msg_meta, MESSAGE_METADATA_TAG_KEY),
        response_id=get_metadata(msg_meta, MESSAGE_METADATA_RESPONSE_ID_KEY) or a2a_message.message_id,
        partial=partial,
        content=genai_types.Content(role="model", parts=parts),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_cancellation_event(
    task_id: str,
    context_id: str,
    message_text: str,
    final: bool = True,
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        status=TaskStatus(
            state=TaskState.canceled,
            timestamp=_now_iso(),
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[TextPart(text=message_text)],
            ),
        ),
        context_id=context_id,
        final=final,
    )


def create_exception_status_event(
    task_id: str,
    context_id: str,
    message_text: str,
    final: bool = True,
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        status=TaskStatus(
            state=TaskState.failed,
            timestamp=_now_iso(),
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[TextPart(text=message_text)],
            ),
        ),
        context_id=context_id,
        final=final,
    )


def create_submitted_status_event(
    task_id: str,
    context_id: str,
    message: Message,
    final: bool = False,
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        status=TaskStatus(state=TaskState.submitted, message=message, timestamp=_now_iso()),
        context_id=context_id,
        final=final,
    )


def create_working_status_event(
    task_id: str,
    context_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    final: bool = False,
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        status=TaskStatus(state=TaskState.working, timestamp=_now_iso()),
        context_id=context_id,
        final=final,
        metadata=metadata,
    )


def create_completed_status_event(
    task_id: str,
    context_id: str,
    final: bool = True,
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        status=TaskStatus(state=TaskState.completed, timestamp=_now_iso()),
        context_id=context_id,
        final=final,
    )


def create_final_status_event(
    task_id: str,
    context_id: str,
    state: TaskState,
    message: Optional[Message] = None,
    final: bool = True,
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        status=TaskStatus(state=state, timestamp=_now_iso(), message=message),
        context_id=context_id,
        final=final,
    )


def _create_error_status_event(
    event: Event,
    ctx: InvocationContext,
    task_id: Optional[str],
    context_id: Optional[str],
) -> TaskStatusUpdateEvent:
    error_message = getattr(event, "error_message", None) or DEFAULT_ERROR_MESSAGE
    event_metadata = _build_context_metadata(event, ctx)
    if event.error_code:
        set_metadata(event_metadata, "error_code", str(event.error_code))

    error_msg_metadata: Dict[str, Any] = {}
    if event.error_code:
        set_metadata(error_msg_metadata, "error_code", str(event.error_code))

    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        metadata=event_metadata,
        status=TaskStatus(
            state=TaskState.failed,
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[TextPart(text=error_message)],
                metadata=error_msg_metadata,
            ),
            timestamp=_now_iso(),
        ),
        final=False,
    )


def _create_status_update_event(
    message: Message,
    ctx: InvocationContext,
    event: Event,
    task_id: Optional[str],
    context_id: Optional[str],
    effective_id: str = "",
) -> TaskStatusUpdateEvent:
    status = TaskStatus(state=TaskState.working, message=message, timestamp=_now_iso())

    if any(
            get_metadata(p.root.metadata, A2A_DATA_PART_METADATA_TYPE_KEY) == A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL
            and metadata_is_true(p.root.metadata, A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY)
            and p.root.data.get("name") == REQUEST_EUC_FUNCTION_CALL_NAME for p in message.parts if p.root.metadata):
        status.state = TaskState.auth_required
    elif any(
            get_metadata(p.root.metadata, A2A_DATA_PART_METADATA_TYPE_KEY) == A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL
            and metadata_is_true(p.root.metadata, A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY) for p in message.parts
            if p.root.metadata):
        status.state = TaskState.input_required

    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=status,
        metadata=_build_event_metadata(event, message, ctx, effective_id),
        final=False,
    )


def _create_artifact_update_event(
    message: Message,
    event: Event,
    ctx: InvocationContext,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
    last_chunk: bool = False,
    effective_id: str = "",
) -> TaskArtifactUpdateEvent:
    artifact_id = "" if last_chunk else effective_id
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        artifact=Artifact(
            artifact_id=artifact_id,
            parts=[] if last_chunk else message.parts,
        ),
        last_chunk=last_chunk,
        metadata=_build_event_metadata(event, message, ctx, effective_id),
    )


def convert_event_to_a2a_events(
    event: Event,
    invocation_context: InvocationContext,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
    on_event: Optional[Callable[[A2AEvent], None]] = None,
) -> List[A2AEvent]:
    """Convert a TrpcAgent Event to A2A events using the artifact-first flow.

    - Errors emit the error message (not wrapped in a status event).
    - Non-final content emits a ``TaskArtifactUpdateEvent``.
    - The ``on_event`` callback (if provided) receives the internal
      ``TaskStatusUpdateEvent`` for state aggregation, regardless of what is
      appended to the returned list.
    """
    if not event:
        raise ValueError("Event cannot be None")
    if not invocation_context:
        raise ValueError("Invocation context cannot be None")

    a2a_events: List[A2AEvent] = []

    def _notify(evt: A2AEvent) -> None:
        if on_event is not None:
            on_event(evt)

    if event.error_code:
        error_event = _create_error_status_event(event, invocation_context, task_id, context_id)
        _notify(error_event)
        if error_event.status and error_event.status.message:
            a2a_events.append(error_event.status.message)

    message = convert_event_to_a2a_message(event, invocation_context)
    if message:
        effective_id = message.message_id
        status_event = _create_status_update_event(
            message,
            invocation_context,
            event,
            task_id,
            context_id,
            effective_id=effective_id,
        )
        _notify(status_event)

        if not event.is_final_response():
            artifact_event = _create_artifact_update_event(
                message,
                event,
                invocation_context,
                task_id=task_id,
                context_id=context_id,
                last_chunk=False,
                effective_id=effective_id,
            )
            a2a_events.append(artifact_event)

    return a2a_events
