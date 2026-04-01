# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Summarizer checker functions."""

from __future__ import annotations

import time
from typing import Callable
from typing import List

from trpc_agent_sdk.log import logger

from ._session import Session

CheckSummarizerFunction = Callable[[Session], bool]


def set_summarizer_token_threshold(token_count: int) -> CheckSummarizerFunction:
    """Set the token threshold for summarizer.

    Args:
        token_count: The token count to check

    Returns:
        True if summarization is needed, False otherwise
    """

    def _decorator(session: Session) -> bool:
        # Filter events with usage_metadata
        events_with_metadata = [event for event in session.events if event.usage_metadata is not None]

        # If no events have usage_metadata, log a warning and return False
        if not events_with_metadata:
            logger.warning(
                "No events with usage_metadata found in session %s. "
                "Token-based summarization check returning False. "
                "This may indicate that LLM responses are not properly recording token usage.", session.id)
            return False

        # Calculate total token count
        total_token_count = sum(event.usage_metadata.total_token_count for event in events_with_metadata)

        return total_token_count > token_count

    return _decorator


def set_summarizer_events_count_threshold(event_count: int = 30) -> CheckSummarizerFunction:
    """Set the number of events threshold for summarizer.

    Args:
        event_count: The event count to check

    Returns:
        True if summarization is needed, False otherwise
    """

    def _decorator(session: Session) -> bool:
        # Check if we have enough events to warrant summarization
        return len(session.events) > event_count

    return _decorator


def set_summarizer_time_interval_threshold(time_interval: float = 300.0) -> CheckSummarizerFunction:
    """Set the time interval threshold for summarizer.

    Args:
        time_interval: The time interval to check

    Returns:
        True if summarization is needed, False otherwise
    """

    def _decorator(session: Session) -> bool:
        # Check if it's been long enough since the last summarization
        return time.time() - session.events[-1].timestamp > time_interval

    return _decorator


def set_summarizer_important_content_threshold(important_content_count: int = 10) -> CheckSummarizerFunction:
    """Set the important content threshold for summarizer.

    Args:
        important_content: The important content to check

    Returns:
        True if summarization is needed, False otherwise
    """

    def _decorator(session: Session) -> bool:
        # Check if there's important content to summarize
        for event in session.events:
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text and len(part.text.strip()) > important_content_count:
                        return True
        return False

    return _decorator


def set_summarizer_conversation_threshold(conversation_count: int = 100) -> CheckSummarizerFunction:
    """Set the conversation count threshold for summarizer.

    Args:
        conversation_count: The conversation count to check

    Returns:
        True if summarization is needed, False otherwise
    """

    def _decorator(session: Session) -> bool:
        if session.conversation_count > conversation_count:
            session.conversation_count = 0
            return True
        return False

    return _decorator


def set_summarizer_check_functions_by_and(funcs: List[CheckSummarizerFunction]) -> CheckSummarizerFunction:
    """Set the check functions for summarizer, all the functions must return True.
    Args:
        funcs: The list of check summarizer functions

    Returns:
        True if summarization is needed, False otherwise
    """

    def _decorator(session: Session) -> bool:
        return all(func(session) for func in funcs)

    return _decorator


def set_summarizer_check_functions_by_or(funcs: List[CheckSummarizerFunction]) -> CheckSummarizerFunction:
    """Set the check functions for summarizer, any of the functions return True.

    Args:
        funcs: The list of check summarizer functions

    Returns:
        True if summarization is needed, False otherwise
    """

    def _decorator(session: Session) -> bool:
        return any(func(session) for func in funcs)

    return _decorator
