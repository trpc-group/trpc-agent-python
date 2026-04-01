# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Session utilities for handling cancellation cleanup.

This module provides utilities for cleaning up session state when
runs are cancelled, ensuring consistency in the session history.
"""

from typing import Optional

from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

_CANCELING_SUFFIX = "Detect user cancel the agent execution."


async def cleanup_incomplete_function_calls(session: SessionABC) -> None:
    """Remove function_calls from session.events that have no matching function_response.

    When cancellation occurs after tool execution, some function_calls may not
    have been executed yet. These incomplete calls should be removed from the
    session to maintain consistency for subsequent conversations.

    Args:
        session: The session to clean up
    """
    # Step 1: Collect all function_response IDs in the session
    response_ids: set[str] = set()
    for event in session.events:
        func_responses = event.get_function_responses()
        for func_response in func_responses:
            response_ids.add(func_response.id)

    # Step 2: Find and remove incomplete function_calls
    for event in session.events:
        func_calls = event.get_function_calls()
        if not func_calls:
            continue

        # Find calls that have no corresponding response
        incomplete_call_ids = {fc.id for fc in func_calls if fc.id not in response_ids}
        if incomplete_call_ids and event.content and event.content.parts:
            # Filter out parts with incomplete function_calls
            original_parts_count = len(event.content.parts)
            event.content.parts = [
                part for part in event.content.parts
                if not (part.function_call and part.function_call.id in incomplete_call_ids)
            ]
            if len(event.content.parts) != original_parts_count:
                logger.debug("Removed %s incomplete function_calls from event %s",
                             original_parts_count - len(event.content.parts), event.id)


async def handle_cancellation_session_cleanup(
    session: SessionABC,
    session_service: SessionServiceABC,
    invocation_id: str,
    agent_name: str,
    branch: Optional[str],
    temp_text: str = "",
) -> None:
    """Handle session cleanup when a run is cancelled.

    This function handles two cancellation scenarios:
    1. Cancelled during LLM streaming (temp_text not empty):
       - Save accumulated partial text with cancellation suffix
    2. Cancelled after tool execution or between turns (temp_text empty):
       - Cleanup incomplete function_calls
       - Add cancellation message

    Args:
        session: The session to update
        session_service: The session service used to persist changes
        invocation_id: The invocation ID of the cancelled run
        agent_name: The name of the agent that was running
        branch: The branch of the event (optional)
        temp_text: Accumulated partial text from streaming (empty if not streaming)
    """
    if temp_text:
        # Scenario A: Cancelled during LLM streaming
        logger.debug("Handling cancellation during LLM streaming (accumulated %s chars)", len(temp_text))
        cancel_content = Content(parts=[Part.from_text(text=f"{temp_text}\n\n{_CANCELING_SUFFIX}")])
        cancel_event = Event(
            invocation_id=invocation_id,
            author=agent_name,
            content=cancel_content,
            branch=branch,
            partial=False,
        )
        await session_service.append_event(session=session, event=cancel_event)
    else:
        # Scenario B: Cancelled after tool execution or between turns
        logger.debug("Handling cancellation after tool execution or between turns")
        await cleanup_incomplete_function_calls(session)
        cancel_event = Event(
            invocation_id=invocation_id,
            author=agent_name,
            content=Content(parts=[Part.from_text(text=_CANCELING_SUFFIX)]),
            branch=branch,
            partial=False,
        )
        await session_service.append_event(session=session, event=cancel_event)
