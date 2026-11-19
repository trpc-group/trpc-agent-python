# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cancel checkpoints and session cleanup for unfinished tool calls."""

from __future__ import annotations

from typing import List
from typing import Optional

from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part

_CANCELING_SUFFIX = "Detect user cancel the agent execution."
_SKIPPED_TOOL_TEMPLATE = "[Tool execution cancelled — {name} was skipped. {reason}]"


async def complete_incomplete_function_calls(
    session: SessionABC,
    session_service: SessionServiceABC,
    *,
    invocation_id: str,
    agent_name: str,
    branch: Optional[str] = None,
    reason: str = "Run cancelled.",
) -> List[FunctionCall]:
    """Append synthetic skipped results for unfilled tool calls.

    OpenAI-style function calling requires every emitted ``function_call`` to
    have a matching ``function_response`` before the next request. When a run
    stops between tool calls, this helper fills the gap instead of deleting the
    assistant's tool-call message.
    """
    response_ids = set()
    for event in session.events:
        for response in event.get_function_responses():
            response_ids.add(response.id)

    incomplete: List[FunctionCall] = []
    seen = set()
    for event in session.events:
        if event.invocation_id != invocation_id:
            continue
        for call in event.get_function_calls():
            if call.id in response_ids or call.id in seen:
                continue
            seen.add(call.id)
            incomplete.append(call)

    if not incomplete:
        return []

    parts: List[Part] = []
    for call in incomplete:
        message = _SKIPPED_TOOL_TEMPLATE.format(name=call.name, reason=reason)
        part = Part.from_function_response(
            name=call.name,
            response={
                "status": "cancelled",
                "message": message,
            },
        )
        if part.function_response is not None:
            part.function_response.id = call.id
        parts.append(part)

    event = Event(
        invocation_id=invocation_id,
        author=agent_name,
        content=Content(role="user", parts=parts),
        branch=branch,
        visible=False,
    )
    await session_service.append_event(session=session, event=event)
    logger.debug("Completed %s skipped tool calls for invocation %s", len(incomplete), invocation_id)
    return incomplete


def build_cancellation_text(temp_text: str = "") -> str:
    """Build the cancellation marker text."""
    if temp_text:
        return f"{temp_text}\n\n{_CANCELING_SUFFIX}"
    return _CANCELING_SUFFIX


__all__ = [
    "build_cancellation_text",
    "complete_incomplete_function_calls",
]
