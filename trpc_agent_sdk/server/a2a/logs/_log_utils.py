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
"""Utility functions for structured A2A request and response logging.

In a2a-sdk 1.x the A2A types are protobuf messages.  ``Part`` uses a oneof
content field (``text`` / ``url`` / ``raw`` / ``data``) and metadata is a
``google.protobuf.Struct``.
"""

from __future__ import annotations

import json

from a2a.types import Message as A2AMessage
from a2a.types import Part as A2APart
from a2a.types import SendMessageRequest
from a2a.types import SendMessageResponse
from a2a.types import Task as A2ATask
from google.protobuf.json_format import MessageToDict

from trpc_agent_sdk.server.a2a._utils import has_field

# Constants
_NEW_LINE = "\n"


def _is_a2a_task(obj) -> bool:
    """Check if an object is an A2A Task, with fallback for isinstance issues."""
    try:
        return isinstance(obj, A2ATask)
    except (TypeError, AttributeError):
        return type(obj).__name__ == "Task" and hasattr(obj, "status")


def _is_a2a_message(obj) -> bool:
    """Check if an object is an A2A Message, with fallback for isinstance issues."""
    try:
        return isinstance(obj, A2AMessage)
    except (TypeError, AttributeError):
        return type(obj).__name__ == "Message" and hasattr(obj, "role")


def _metadata_dict(metadata) -> dict:
    """Convert a Struct/dict metadata value to a plain dict for logging."""
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    return MessageToDict(metadata)


def _is_a2a_text_part(part: A2APart) -> bool:
    """Check if a protobuf Part is a text part."""
    return has_field(part, "text")


def _is_a2a_data_part(part: A2APart) -> bool:
    """Check if a protobuf Part is a data part."""
    return has_field(part, "data")


def build_message_part_log(part: A2APart) -> str:
    """Builds a log representation of an A2A message part.

    Args:
        part: The A2A message part to log.

    Returns:
        A string representation of the part.
    """
    part_content = ""
    if _is_a2a_text_part(part):
        text = part.text
        part_content = f"TextPart: {text[:100]}" + ("..." if len(text) > 100 else "")
    elif _is_a2a_data_part(part):
        # For data parts, show the data keys but exclude large values
        data_summary = _metadata_dict(part.data)
        if not isinstance(data_summary, dict):
            data_summary = {"value": data_summary}
        summarized = {
            k: (f"<{type(v).__name__}>" if isinstance(v, (dict, list)) and len(str(v)) > 100 else v)
            for k, v in data_summary.items()
        }
        part_content = f"DataPart: {json.dumps(summarized, indent=2)}"
    elif has_field(part, "url"):
        part_content = f"FilePart: url={part.url}, media_type={part.media_type}"
    elif has_field(part, "raw"):
        part_content = f"FilePart: raw bytes ({len(part.raw)} bytes), media_type={part.media_type}"
    else:
        part_content = f"Part: {type(part).__name__}"

    # Add part metadata if it exists
    if has_field(part, "metadata") and part.metadata:
        metadata_str = json.dumps(_metadata_dict(part.metadata), indent=2).replace("\n", "\n    ")
        part_content += f"\n    Part Metadata: {metadata_str}"

    return part_content


def _build_message_section(message: A2AMessage, indent: str = "") -> str:
    """Build a structured log section for an A2A Message."""
    parts_logs = []
    for i, part in enumerate(message.parts):
        part_log = build_message_part_log(part)
        part_log_formatted = part_log.replace("\n", "\n" + indent + "  ")
        parts_logs.append(f"{indent}  Part {i}: {part_log_formatted}")

    metadata_section = ""
    if message.metadata:
        meta = _metadata_dict(message.metadata)
        metadata_section = f"""
{indent}  Metadata:
{indent}  {json.dumps(meta, indent=2).replace(chr(10), chr(10) + indent + '  ')}"""

    return f"""{indent}  ID: {message.message_id}
{indent}  Role: {message.role}
{indent}  Task ID: {message.task_id}
{indent}  Context ID: {message.context_id}
{indent}  Message Parts:
{_NEW_LINE.join(parts_logs) if parts_logs else indent + '  No parts'}{metadata_section}"""


def build_a2a_request_log(req: SendMessageRequest) -> str:
    """Builds a structured log representation of an A2A request.

    Args:
        req: The A2A SendMessageRequest to log.

    Returns:
        A formatted string representation of the request.
    """
    message = req.message if req.HasField("message") else None
    message_section = _build_message_section(message) if message else "  No message"

    # Configuration logs
    config_log = "None"
    if req.HasField("configuration"):
        config = req.configuration
        config_data = {
            "accepted_output_modes": list(config.accepted_output_modes),
            "return_immediately": config.return_immediately,
            "history_length": config.history_length,
            "push_notification_config": bool(config.HasField("task_push_notification_config")),
        }
        config_log = json.dumps(config_data, indent=2)

    # Build optional sections
    optional_sections = []

    if req.HasField("metadata") and req.metadata:
        optional_sections.append(f"""-----------------------------------------------------------
Metadata:
{json.dumps(_metadata_dict(req.metadata), indent=2)}""")

    optional_sections_str = _NEW_LINE.join(optional_sections)

    return f"""
A2A Request:
-----------------------------------------------------------
Tenant: {req.tenant}
Message:
{message_section}
-----------------------------------------------------------
Configuration:
{config_log}
{optional_sections_str}
-----------------------------------------------------------
"""


def _jsonrpc_error_attr(error, name: str, default=None):
    """Read ``code`` / ``message`` / ``data`` from an error object or dict."""
    if isinstance(error, dict):
        return error.get(name, default)
    return getattr(error, name, default)


def _format_error_data(data) -> str:
    """Serialize JSON-RPC error data for logging."""
    if data is None:
        return "None"
    if hasattr(data, "DESCRIPTOR"):
        try:
            data = MessageToDict(data)
        except (TypeError, ValueError, AttributeError):
            return str(data)
    try:
        return json.dumps(data, indent=2)
    except TypeError:
        return str(data)


def _extract_jsonrpc_error(resp) -> tuple[object, object | None, object | None] | None:
    """Extract ``(error, id, jsonrpc)`` from a JSON-RPC error envelope.

    1.x protobuf ``SendMessageResponse`` has no error payload; errors arrive as
    ``JSONRPCErrorResponse``, 0.3 RootModel ``SendMessageResponse``, a dict
    from ``build_error_response``, or a proto with an ``error`` oneof.
    """
    if has_field(resp, "error"):
        return (
            resp.error,
            getattr(resp, "id", None),
            getattr(resp, "jsonrpc", None),
        )

    root = getattr(resp, "root", None)
    if root is not None:
        error = getattr(root, "error", None)
        if error is not None:
            return error, getattr(root, "id", None), getattr(root, "jsonrpc", None)

    error = getattr(resp, "error", None)
    if error is not None:
        return error, getattr(resp, "id", None), getattr(resp, "jsonrpc", None)

    if isinstance(resp, dict) and resp.get("error") is not None:
        return resp["error"], resp.get("id"), resp.get("jsonrpc")

    return None


def build_a2a_response_log(resp: SendMessageResponse) -> str:
    """Builds a structured log representation of an A2A response.

    Args:
        resp: The A2A SendMessageResponse to log, or a JSON-RPC error envelope.

    Returns:
        A formatted string representation of the response.
    """
    error_info = _extract_jsonrpc_error(resp)
    if error_info is not None:
        error, response_id, jsonrpc = error_info
        return f"""
A2A Response:
-----------------------------------------------------------
Type: ERROR
Error Code: {_jsonrpc_error_attr(error, "code")}
Error Message: {_jsonrpc_error_attr(error, "message")}
Error Data: {_format_error_data(_jsonrpc_error_attr(error, "data"))}
-----------------------------------------------------------
Response ID: {response_id}
JSON-RPC: {jsonrpc}
-----------------------------------------------------------
"""

    result = None
    if has_field(resp, "task"):
        result = resp.task
    elif has_field(resp, "message"):
        result = resp.message
    result_type = type(result).__name__ if result else "None"

    result_details = []
    if result is None:
        result_details.append("No result")

    elif _is_a2a_task(result):
        result_details.extend([
            f"Task ID: {result.id}",
            f"Context ID: {result.context_id}",
            f"Status State: {result.status.state}",
            f"Status Timestamp: {result.status.timestamp}",
            f"History Length: {len(result.history) if result.history else 0}",
            f"Artifacts Count: {len(result.artifacts) if result.artifacts else 0}",
        ])

        if result.metadata:
            result_details.append("Task Metadata:")
            metadata_formatted = json.dumps(_metadata_dict(result.metadata), indent=2).replace("\n", "\n  ")
            result_details.append(f"  {metadata_formatted}")

    elif _is_a2a_message(result):
        result_details.extend([
            f"Message ID: {result.message_id}",
            f"Role: {result.role}",
            f"Task ID: {result.task_id}",
            f"Context ID: {result.context_id}",
        ])

        if result.parts:
            result_details.append("Message Parts:")
            for i, part in enumerate(result.parts):
                part_log = build_message_part_log(part)
                part_log_formatted = part_log.replace("\n", "\n    ")
                result_details.append(f"  Part {i}: {part_log_formatted}")

        if result.metadata:
            result_details.append("Metadata:")
            metadata_formatted = json.dumps(_metadata_dict(result.metadata), indent=2).replace("\n", "\n  ")
            result_details.append(f"  {metadata_formatted}")

    # Build status message section
    status_message_section = "None"
    if _is_a2a_task(result) and result.status.HasField("message"):
        status_message_section = _build_message_section(result.status.message, indent="")

    # Build history section
    history_section = "No history"
    if _is_a2a_task(result) and result.history:
        history_logs = []
        for i, message in enumerate(result.history):
            history_logs.append(f"Message {i + 1}:\n{_build_message_section(message, indent='  ')}")
        history_section = _NEW_LINE.join(history_logs)

    return f"""
A2A Response:
-----------------------------------------------------------
Type: SUCCESS
Result Type: {result_type}
-----------------------------------------------------------
Result Details:
{_NEW_LINE.join(result_details)}
-----------------------------------------------------------
Status Message:
{status_message_section}
-----------------------------------------------------------
History:
{history_section}
-----------------------------------------------------------
"""
