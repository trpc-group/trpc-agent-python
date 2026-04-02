# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for LangGraph event writer utilities."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from trpc_agent_sdk.agents.utils._langgraph_event_writer import (
    LANGGRAPH_EVENT_TYPE,
    LangGraphEventType,
    LangGraphEventWriter,
    TRPC_EVENT_MARKER,
    _TrpcEventWrapper,
    extract_trpc_event,
    get_event_type,
    is_trpc_event_chunk,
)
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content, Part


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_trpc_event_marker(self):
        assert TRPC_EVENT_MARKER == "__trpc_event__"

    def test_langgraph_event_type_key(self):
        assert LANGGRAPH_EVENT_TYPE == "__langgraph_event_type__"


# ---------------------------------------------------------------------------
# LangGraphEventType enum
# ---------------------------------------------------------------------------


class TestLangGraphEventType:
    def test_text_value(self):
        assert LangGraphEventType.TEXT == "text"

    def test_custom_value(self):
        assert LangGraphEventType.CUSTOM == "custom"


# ---------------------------------------------------------------------------
# _TrpcEventWrapper
# ---------------------------------------------------------------------------


class TestTrpcEventWrapper:
    def test_to_dict_contains_marker(self):
        event = Event(invocation_id="inv-1", author="agent")
        wrapper = _TrpcEventWrapper(event, LangGraphEventType.TEXT)
        d = wrapper.to_dict()
        assert d[TRPC_EVENT_MARKER] is True
        assert d[LANGGRAPH_EVENT_TYPE] == "text"
        assert d["event"] is event

    def test_custom_event_type(self):
        event = Event(invocation_id="inv-1", author="agent")
        wrapper = _TrpcEventWrapper(event, LangGraphEventType.CUSTOM)
        d = wrapper.to_dict()
        assert d[LANGGRAPH_EVENT_TYPE] == "custom"


# ---------------------------------------------------------------------------
# is_trpc_event_chunk
# ---------------------------------------------------------------------------


class TestIsTrpcEventChunk:
    def test_true_for_valid_chunk(self):
        chunk = {TRPC_EVENT_MARKER: True, "event": Event(invocation_id="inv-1", author="a")}
        assert is_trpc_event_chunk(chunk) is True

    def test_false_for_missing_marker(self):
        chunk = {"other": "data"}
        assert is_trpc_event_chunk(chunk) is False

    def test_false_for_non_dict(self):
        assert is_trpc_event_chunk("string") is False
        assert is_trpc_event_chunk(None) is False
        assert is_trpc_event_chunk(42) is False

    def test_false_for_marker_false(self):
        chunk = {TRPC_EVENT_MARKER: False}
        assert is_trpc_event_chunk(chunk) is False


# ---------------------------------------------------------------------------
# extract_trpc_event
# ---------------------------------------------------------------------------


class TestExtractTrpcEvent:
    def test_extracts_event(self):
        event = Event(invocation_id="inv-1", author="test")
        chunk = {TRPC_EVENT_MARKER: True, "event": event}
        extracted = extract_trpc_event(chunk)
        assert extracted is event

    def test_raises_for_invalid_chunk(self):
        with pytest.raises(ValueError, match="does not contain"):
            extract_trpc_event({"other": "data"})

    def test_raises_for_invalid_event_type(self):
        chunk = {TRPC_EVENT_MARKER: True, "event": "not_an_event"}
        with pytest.raises(ValueError, match="Invalid event"):
            extract_trpc_event(chunk)


# ---------------------------------------------------------------------------
# get_event_type
# ---------------------------------------------------------------------------


class TestGetEventType:
    def test_returns_text_type(self):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            custom_metadata={LANGGRAPH_EVENT_TYPE: "text"},
        )
        assert get_event_type(event) == LangGraphEventType.TEXT

    def test_returns_custom_type(self):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            custom_metadata={LANGGRAPH_EVENT_TYPE: "custom"},
        )
        assert get_event_type(event) == LangGraphEventType.CUSTOM

    def test_returns_none_for_no_metadata(self):
        event = Event(invocation_id="inv-1", author="agent")
        assert get_event_type(event) is None

    def test_returns_none_for_unknown_type(self):
        event = Event(
            invocation_id="inv-1",
            author="agent",
            custom_metadata={LANGGRAPH_EVENT_TYPE: "unknown"},
        )
        assert get_event_type(event) is None


# ---------------------------------------------------------------------------
# LangGraphEventWriter
# ---------------------------------------------------------------------------


class TestLangGraphEventWriter:
    @pytest.fixture
    def mock_ctx(self):
        ctx = Mock(spec=InvocationContext)
        ctx.invocation_id = "inv-1"
        ctx.agent_name = "test_agent"
        ctx.branch = "test_branch"
        return ctx

    @pytest.fixture
    def mock_writer(self):
        return Mock()

    def test_write_text(self, mock_ctx, mock_writer):
        writer = LangGraphEventWriter(mock_writer, mock_ctx)
        writer.write_text("Hello")
        mock_writer.assert_called_once()
        call_args = mock_writer.call_args[0][0]
        assert call_args[TRPC_EVENT_MARKER] is True
        assert call_args[LANGGRAPH_EVENT_TYPE] == "text"

    def test_write_text_thought(self, mock_ctx, mock_writer):
        writer = LangGraphEventWriter(mock_writer, mock_ctx)
        writer.write_text("thinking...", thought=True)
        mock_writer.assert_called_once()

    def test_write_custom(self, mock_ctx, mock_writer):
        writer = LangGraphEventWriter(mock_writer, mock_ctx)
        writer.write_custom({"progress": 50})
        mock_writer.assert_called_once()
        call_args = mock_writer.call_args[0][0]
        assert call_args[TRPC_EVENT_MARKER] is True
        assert call_args[LANGGRAPH_EVENT_TYPE] == "custom"

    def test_from_config(self):
        from trpc_agent_sdk.agents.utils._langgraph import TRPC_AGENT_KEY, AGENT_CTX_KEY

        mock_writer = Mock()
        mock_ctx = Mock(spec=InvocationContext)
        config = {"configurable": {TRPC_AGENT_KEY: {AGENT_CTX_KEY: mock_ctx}}}
        event_writer = LangGraphEventWriter.from_config(mock_writer, config)
        assert event_writer._ctx is mock_ctx

    def test_from_config_raises_without_context(self):
        mock_writer = Mock()
        with pytest.raises(ValueError):
            LangGraphEventWriter.from_config(mock_writer, {})
