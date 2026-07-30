# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Utils for events."""

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._event import Event


def create_text_event(
    ctx: InvocationContext,
    text: str,
    thought_text: str = None,
    visible: bool = True,
    save: bool = False,
) -> Event:
    """Create a text event with the given content.

    Args:
        ctx: The invocation context containing invocation information.
        text: The text content for the event.
        thought_text: The thought text content for the event.
        visible: Whether this event should be visible to user (default: True).
        save: Whether this event should be saved to session (default: False).

    Returns:
        Event: A new event with text content and proper field initialization.
    """
    # Create content with a text part
    parts = []
    if thought_text:
        parts.append(Part(text=thought_text, thought=True))
    parts.append(Part(text=text))
    content = Content(role="model", parts=parts)

    # Create and return the event with all necessary fields
    return Event(
        invocation_id=ctx.invocation_id,
        author=ctx.agent.name,
        content=content,
        branch=ctx.branch,
        visible=visible,
        partial=save,
    )
