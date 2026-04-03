# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.sessions._summarizer_checker.

Covers:
- set_summarizer_token_threshold
- set_summarizer_events_count_threshold
- set_summarizer_time_interval_threshold
- set_summarizer_important_content_threshold
- set_summarizer_conversation_threshold
- set_summarizer_check_functions_by_and
- set_summarizer_check_functions_by_or
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._summarizer_checker import (
    set_summarizer_check_functions_by_and,
    set_summarizer_check_functions_by_or,
    set_summarizer_conversation_threshold,
    set_summarizer_events_count_threshold,
    set_summarizer_important_content_threshold,
    set_summarizer_time_interval_threshold,
    set_summarizer_token_threshold,
)
from trpc_agent_sdk.types import Content, EventActions, Part


def _make_session(events=None, conversation_count=0) -> Session:
    s = Session(id="s1", app_name="app", user_id="user", save_key="app/user")
    s.events = events or []
    s.conversation_count = conversation_count
    return s


def _make_event_with_usage(total_tokens: int) -> Event:
    mock_usage = MagicMock()
    mock_usage.total_token_count = total_tokens
    event = Event(
        invocation_id="inv-1",
        author="agent",
        content=Content(parts=[Part.from_text(text="test")]),
    )
    event.usage_metadata = mock_usage
    return event


def _make_event_with_text(text: str) -> Event:
    return Event(
        invocation_id="inv-1",
        author="agent",
        content=Content(parts=[Part.from_text(text=text)]),
    )


class TestTokenThreshold:
    """Test set_summarizer_token_threshold."""

    def test_above_threshold(self):
        checker = set_summarizer_token_threshold(100)
        events = [_make_event_with_usage(60), _make_event_with_usage(50)]
        session = _make_session(events=events)
        assert checker(session) is True

    def test_below_threshold(self):
        checker = set_summarizer_token_threshold(100)
        events = [_make_event_with_usage(30), _make_event_with_usage(20)]
        session = _make_session(events=events)
        assert checker(session) is False

    def test_exact_threshold(self):
        checker = set_summarizer_token_threshold(100)
        events = [_make_event_with_usage(50), _make_event_with_usage(50)]
        session = _make_session(events=events)
        assert checker(session) is False

    def test_no_usage_metadata(self):
        checker = set_summarizer_token_threshold(100)
        events = [_make_event_with_text("hello")]
        session = _make_session(events=events)
        assert checker(session) is False


class TestEventsCountThreshold:
    """Test set_summarizer_events_count_threshold."""

    def test_above_threshold(self):
        checker = set_summarizer_events_count_threshold(5)
        events = [_make_event_with_text(f"msg{i}") for i in range(6)]
        session = _make_session(events=events)
        assert checker(session) is True

    def test_below_threshold(self):
        checker = set_summarizer_events_count_threshold(5)
        events = [_make_event_with_text(f"msg{i}") for i in range(3)]
        session = _make_session(events=events)
        assert checker(session) is False

    def test_exact_threshold(self):
        checker = set_summarizer_events_count_threshold(5)
        events = [_make_event_with_text(f"msg{i}") for i in range(5)]
        session = _make_session(events=events)
        assert checker(session) is False

    def test_default_threshold(self):
        checker = set_summarizer_events_count_threshold()
        events = [_make_event_with_text(f"msg{i}") for i in range(31)]
        session = _make_session(events=events)
        assert checker(session) is True


class TestTimeIntervalThreshold:
    """Test set_summarizer_time_interval_threshold."""

    def test_above_threshold(self):
        checker = set_summarizer_time_interval_threshold(10.0)
        event = _make_event_with_text("old")
        event.timestamp = time.time() - 20.0
        session = _make_session(events=[event])
        assert checker(session) is True

    def test_below_threshold(self):
        checker = set_summarizer_time_interval_threshold(10.0)
        event = _make_event_with_text("recent")
        event.timestamp = time.time() - 1.0
        session = _make_session(events=[event])
        assert checker(session) is False

    def test_default_threshold(self):
        checker = set_summarizer_time_interval_threshold()
        event = _make_event_with_text("recent")
        event.timestamp = time.time() - 1.0
        session = _make_session(events=[event])
        assert checker(session) is False


class TestImportantContentThreshold:
    """Test set_summarizer_important_content_threshold."""

    def test_has_important_content(self):
        checker = set_summarizer_important_content_threshold(5)
        events = [_make_event_with_text("This is important content")]
        session = _make_session(events=events)
        assert checker(session) is True

    def test_no_important_content(self):
        checker = set_summarizer_important_content_threshold(100)
        events = [_make_event_with_text("short")]
        session = _make_session(events=events)
        assert checker(session) is False

    def test_empty_events(self):
        checker = set_summarizer_important_content_threshold(5)
        session = _make_session(events=[])
        assert checker(session) is False

    def test_event_without_content(self):
        checker = set_summarizer_important_content_threshold(5)
        event = Event(invocation_id="inv-1", author="agent", actions=EventActions())
        session = _make_session(events=[event])
        assert checker(session) is False

    def test_event_with_empty_parts(self):
        checker = set_summarizer_important_content_threshold(5)
        event = Event(invocation_id="inv-1", author="agent", content=Content(parts=[]), actions=EventActions())
        session = _make_session(events=[event])
        assert checker(session) is False

    def test_event_with_whitespace_only(self):
        checker = set_summarizer_important_content_threshold(5)
        events = [_make_event_with_text("   ")]
        session = _make_session(events=events)
        assert checker(session) is False


class TestConversationThreshold:
    """Test set_summarizer_conversation_threshold."""

    def test_above_threshold(self):
        checker = set_summarizer_conversation_threshold(10)
        session = _make_session(conversation_count=15)
        assert checker(session) is True
        assert session.conversation_count == 0

    def test_below_threshold(self):
        checker = set_summarizer_conversation_threshold(10)
        session = _make_session(conversation_count=5)
        assert checker(session) is False

    def test_exact_threshold(self):
        checker = set_summarizer_conversation_threshold(10)
        session = _make_session(conversation_count=10)
        assert checker(session) is False

    def test_default_threshold(self):
        checker = set_summarizer_conversation_threshold()
        session = _make_session(conversation_count=101)
        assert checker(session) is True

    def test_resets_count_on_true(self):
        checker = set_summarizer_conversation_threshold(5)
        session = _make_session(conversation_count=10)
        result = checker(session)
        assert result is True
        assert session.conversation_count == 0


class TestCheckFunctionsByAnd:
    """Test set_summarizer_check_functions_by_and."""

    def test_all_true(self):
        f1 = lambda s: True
        f2 = lambda s: True
        checker = set_summarizer_check_functions_by_and([f1, f2])
        session = _make_session()
        assert checker(session) is True

    def test_one_false(self):
        f1 = lambda s: True
        f2 = lambda s: False
        checker = set_summarizer_check_functions_by_and([f1, f2])
        session = _make_session()
        assert checker(session) is False

    def test_all_false(self):
        f1 = lambda s: False
        f2 = lambda s: False
        checker = set_summarizer_check_functions_by_and([f1, f2])
        session = _make_session()
        assert checker(session) is False

    def test_empty_list(self):
        checker = set_summarizer_check_functions_by_and([])
        session = _make_session()
        assert checker(session) is True


class TestCheckFunctionsByOr:
    """Test set_summarizer_check_functions_by_or."""

    def test_all_true(self):
        f1 = lambda s: True
        f2 = lambda s: True
        checker = set_summarizer_check_functions_by_or([f1, f2])
        session = _make_session()
        assert checker(session) is True

    def test_one_true(self):
        f1 = lambda s: False
        f2 = lambda s: True
        checker = set_summarizer_check_functions_by_or([f1, f2])
        session = _make_session()
        assert checker(session) is True

    def test_all_false(self):
        f1 = lambda s: False
        f2 = lambda s: False
        checker = set_summarizer_check_functions_by_or([f1, f2])
        session = _make_session()
        assert checker(session) is False

    def test_empty_list(self):
        checker = set_summarizer_check_functions_by_or([])
        session = _make_session()
        assert checker(session) is False
