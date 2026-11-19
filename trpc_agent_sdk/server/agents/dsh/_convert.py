# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Convert DSH frames and session events into tRPC-Agent events."""

from __future__ import annotations

import json
from typing import Any

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from ._protocol import Frame


def _event(
    *,
    invocation_id: str,
    author: str,
    branch: str | None,
    parts: list[Part],
    partial: bool,
) -> Event:
    return Event(
        invocation_id=invocation_id,
        author=author,
        branch=branch,
        content=Content(role="model", parts=parts),
        partial=partial,
    )


def _delta_event(
    data: dict[str, Any],
    *,
    invocation_id: str,
    author: str,
    branch: str | None,
) -> Event | None:
    kind = data.get("kind")
    text = data.get("text")
    if not isinstance(text, str) or text == "":
        return None
    if kind == "text":
        return _event(
            invocation_id=invocation_id,
            author=author,
            branch=branch,
            parts=[Part(text=text)],
            partial=True,
        )
    if kind == "reasoning":
        return _event(
            invocation_id=invocation_id,
            author=author,
            branch=branch,
            parts=[Part(text=text, thought=True)],
            partial=True,
        )
    return None


def _tool_call_part(data: dict[str, Any], call_names: dict[str, str]) -> Part | None:
    call_id = data.get("callId")
    name = data.get("name")
    if not isinstance(call_id, str) or not isinstance(name, str):
        return None
    arguments = data.get("arguments")
    try:
        args = json.loads(arguments) if isinstance(arguments, str) and arguments else {}
    except json.JSONDecodeError:
        args = {"raw": arguments}
    if not isinstance(args, dict):
        args = {"value": args}
    call_names[call_id] = name
    return Part(function_call=FunctionCall(id=call_id, name=name, args=args))


def _tool_result_part(data: dict[str, Any], call_names: dict[str, str]) -> Part | None:
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    call_id = message.get("toolCallId")
    if not isinstance(call_id, str):
        return None
    texts: list[str] = []
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                texts.append(block["text"])
    response: dict[str, Any] = {"result": "".join(texts)}
    if message.get("isError") is True:
        response["is_error"] = True
    return Part(function_response=FunctionResponse(
        id=call_id,
        name=call_names.get(call_id, "tool"),
        response=response,
    ))


def _assistant_message_event(
    data: dict[str, Any],
    *,
    invocation_id: str,
    author: str,
    branch: str | None,
) -> Event | None:
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    parts: list[Part] = []
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                parts.append(Part(text=block["text"]))
            elif block_type == "reasoning" and isinstance(block.get("text"), str):
                parts.append(Part(text=block["text"], thought=True))
    if not parts:
        return None
    return _event(
        invocation_id=invocation_id,
        author=author,
        branch=branch,
        parts=parts,
        partial=False,
    )


def _session_event(
    event: dict[str, Any],
    *,
    invocation_id: str,
    author: str,
    branch: str | None,
    call_names: dict[str, str],
) -> Event | None:
    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(data, dict):
        return None

    if event_type == "assistant/message":
        return _assistant_message_event(
            data,
            invocation_id=invocation_id,
            author=author,
            branch=branch,
        )
    if event_type == "tool/call":
        part = _tool_call_part(data, call_names)
        if part is None:
            return None
        return _event(
            invocation_id=invocation_id,
            author=author,
            branch=branch,
            parts=[part],
            partial=False,
        )
    if event_type == "tool/result":
        part = _tool_result_part(data, call_names)
        if part is None:
            return None
        return _event(
            invocation_id=invocation_id,
            author=author,
            branch=branch,
            parts=[part],
            partial=False,
        )
    return None


def to_trpc_event(
    frame: Frame,
    *,
    invocation_id: str,
    author: str,
    branch: str | None,
    call_names: dict[str, str],
) -> Event | None:
    """Convert one DSH frame into a tRPC event, or ``None`` for non-surface frames."""

    if frame.kind == "delta":
        return _delta_event(
            frame.data,
            invocation_id=invocation_id,
            author=author,
            branch=branch,
        )
    if frame.kind == "event":
        return _session_event(
            frame.data,
            invocation_id=invocation_id,
            author=author,
            branch=branch,
            call_names=call_names,
        )
    return None
