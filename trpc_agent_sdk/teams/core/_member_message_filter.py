# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Member Message Filter for TeamAgent.

This module provides the MessageFilter type and built-in filter functions
for filtering member responses before they're added to the leader's context.

The filter functions receive a list of Content objects from member execution
and return a filtered text string to be used in the delegation record.
"""

from __future__ import annotations

from typing import Awaitable
from typing import Callable
from typing import List
from typing import TypeAlias
from typing import Union

from trpc_agent_sdk.types import Content

# TeamMemberMessageFilter: function that filters messages and returns filtered text
# Can be sync or async
TeamMemberMessageFilter: TypeAlias = Callable[[List[Content]], Union[str, Awaitable[str]]]


async def keep_all_member_message(messages: List[Content]) -> str:
    """Keep all text from all member messages.

    This is the default filter that preserves the current behavior.
    Extracts text from all Content parts (excluding thoughts) and joins them.

    Args:
        messages: List of Content objects from member execution.

    Returns:
        All text content joined with newlines.
    """
    texts = []
    for content in messages:
        if not content or not content.parts:
            continue
        for part in content.parts:
            # Skip thought content
            if part.thought:
                continue
            if part.text:
                texts.append(part.text)
            elif part.function_call:
                fc = part.function_call
                texts.append(f"[Tool Call: {fc.name}({fc.args})]")
            elif part.function_response:
                fr = part.function_response
                texts.append(f"[Tool Result: {fr.response}]")

    return "\n".join(texts) if texts else ""


async def keep_last_member_message(messages: List[Content]) -> str:
    """Keep only text from the last member message.

    Useful when you only want the final response from a member,
    ignoring intermediate tool calls and responses.

    Args:
        messages: List of Content objects from member execution.

    Returns:
        Text content from the last message only.
    """
    if not messages:
        return ""

    # Find the last message with text content (not just tool calls/responses)
    for content in reversed(messages):
        if not content or not content.parts:
            continue

        texts = []
        for part in content.parts:
            # Skip thought content
            if part.thought:
                continue
            if part.text:
                texts.append(part.text)

        if texts:
            return "\n".join(texts)

    return ""
