# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.cancel._session_utils.

Covers:
- cleanup_incomplete_function_calls (removes orphan function_calls)
- handle_cancellation_session_cleanup (streaming vs non-streaming cancellation)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.cancel._session_utils import (
    _CANCELING_SUFFIX,
    cleanup_incomplete_function_calls,
    handle_cancellation_session_cleanup,
)
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part


def _make_event(
    author: str = "agent",
    text: str = "",
    invocation_id: str = "inv-1",
    function_calls: list[FunctionCall] | None = None,
    function_responses: list[FunctionResponse] | None = None,
) -> Event:
    """Helper to build Event instances for testing."""
    parts = []
    if text:
        parts.append(Part(text=text))
    if function_calls:
        for fc in function_calls:
            parts.append(Part(function_call=fc))
    if function_responses:
        for fr in function_responses:
            parts.append(Part(function_response=fr))
    return Event(
        invocation_id=invocation_id,
        author=author,
        content=Content(parts=parts) if parts else None,
    )


def _make_session(events: list[Event]):
    """Create a mock session object with given events."""
    session = MagicMock()
    session.events = events
    return session


def _make_session_service():
    """Create a mock session service."""
    service = AsyncMock()
    service.append_event = AsyncMock()
    return service


# ---------------------------------------------------------------------------
# cleanup_incomplete_function_calls
# ---------------------------------------------------------------------------
class TestCleanupIncompleteFunctionCalls:
    """Tests for cleanup_incomplete_function_calls."""

    async def test_no_events(self):
        session = _make_session([])
        await cleanup_incomplete_function_calls(session)
        assert session.events == []

    async def test_no_function_calls(self):
        events = [_make_event(text="hello")]
        session = _make_session(events)
        await cleanup_incomplete_function_calls(session)
        assert len(session.events) == 1

    async def test_complete_function_call_kept(self):
        """A function_call with a matching function_response is kept."""
        fc = FunctionCall(id="fc1", name="tool1", args={"a": 1})
        fr = FunctionResponse(id="fc1", name="tool1", response={"result": "ok"})
        event_call = _make_event(function_calls=[fc])
        event_response = _make_event(function_responses=[fr])
        session = _make_session([event_call, event_response])

        await cleanup_incomplete_function_calls(session)

        remaining_calls = session.events[0].get_function_calls()
        assert len(remaining_calls) == 1
        assert remaining_calls[0].id == "fc1"

    async def test_incomplete_function_call_removed(self):
        """A function_call without matching function_response is removed."""
        fc = FunctionCall(id="fc1", name="tool1", args={"a": 1})
        event = _make_event(function_calls=[fc])
        session = _make_session([event])

        await cleanup_incomplete_function_calls(session)

        remaining_calls = session.events[0].get_function_calls()
        assert len(remaining_calls) == 0

    async def test_mixed_complete_and_incomplete(self):
        """Only incomplete calls are removed; complete ones stay."""
        fc1 = FunctionCall(id="fc1", name="tool1", args={})
        fc2 = FunctionCall(id="fc2", name="tool2", args={})
        fr1 = FunctionResponse(id="fc1", name="tool1", response={})
        event_call = _make_event(function_calls=[fc1, fc2])
        event_response = _make_event(function_responses=[fr1])
        session = _make_session([event_call, event_response])

        await cleanup_incomplete_function_calls(session)

        remaining_calls = session.events[0].get_function_calls()
        assert len(remaining_calls) == 1
        assert remaining_calls[0].id == "fc1"

    async def test_text_parts_preserved(self):
        """Text parts in the same event are not removed."""
        fc = FunctionCall(id="fc_orphan", name="tool", args={})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[
                Part(text="some text"),
                Part(function_call=fc),
            ]),
        )
        session = _make_session([event])

        await cleanup_incomplete_function_calls(session)

        assert len(event.content.parts) == 1
        assert event.content.parts[0].text == "some text"

    async def test_multiple_events_with_orphans(self):
        """Orphan calls across multiple events are all cleaned up."""
        fc1 = FunctionCall(id="fc1", name="t1", args={})
        fc2 = FunctionCall(id="fc2", name="t2", args={})
        fr2 = FunctionResponse(id="fc2", name="t2", response={})
        e1 = _make_event(function_calls=[fc1])
        e2 = _make_event(function_calls=[fc2])
        e3 = _make_event(function_responses=[fr2])
        session = _make_session([e1, e2, e3])

        await cleanup_incomplete_function_calls(session)

        assert len(session.events[0].get_function_calls()) == 0
        assert len(session.events[1].get_function_calls()) == 1

    async def test_event_with_none_content(self):
        """Events with None content are handled safely."""
        event = Event(invocation_id="inv-1", author="agent", content=None)
        session = _make_session([event])
        await cleanup_incomplete_function_calls(session)

    async def test_event_with_empty_parts(self):
        """Events with empty parts list are handled safely."""
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[]),
        )
        session = _make_session([event])
        await cleanup_incomplete_function_calls(session)

    async def test_all_calls_complete_no_change(self):
        """When all calls have responses, nothing is removed."""
        fc1 = FunctionCall(id="fc1", name="t1", args={})
        fc2 = FunctionCall(id="fc2", name="t2", args={})
        fr1 = FunctionResponse(id="fc1", name="t1", response={})
        fr2 = FunctionResponse(id="fc2", name="t2", response={})
        e1 = _make_event(function_calls=[fc1, fc2])
        e2 = _make_event(function_responses=[fr1, fr2])
        session = _make_session([e1, e2])

        await cleanup_incomplete_function_calls(session)

        assert len(session.events[0].get_function_calls()) == 2


# ---------------------------------------------------------------------------
# handle_cancellation_session_cleanup
# ---------------------------------------------------------------------------
class TestHandleCancellationSessionCleanup:
    """Tests for handle_cancellation_session_cleanup."""

    async def test_streaming_cancellation_saves_partial_text(self):
        """Scenario A: cancelled during streaming — saves accumulated text + suffix."""
        session = _make_session([])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-1",
            agent_name="my_agent",
            branch=None,
            temp_text="partial response",
        )

        service.append_event.assert_called_once()
        event_arg = service.append_event.call_args.kwargs.get("event") or service.append_event.call_args[1].get("event", service.append_event.call_args[0][1] if len(service.append_event.call_args[0]) > 1 else None)
        assert event_arg is not None
        text = event_arg.get_text()
        assert "partial response" in text
        assert _CANCELING_SUFFIX in text

    async def test_streaming_cancellation_event_metadata(self):
        """Verify event fields in streaming cancellation."""
        session = _make_session([])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-42",
            agent_name="agent_x",
            branch="branch_a",
            temp_text="text",
        )

        call_args = service.append_event.call_args
        event_arg = call_args.kwargs.get("event") or call_args[1].get("event", call_args[0][1] if len(call_args[0]) > 1 else None)
        assert event_arg.invocation_id == "inv-42"
        assert event_arg.author == "agent_x"
        assert event_arg.branch == "branch_a"
        assert event_arg.partial is False

    async def test_non_streaming_cancellation_adds_cancel_message(self):
        """Scenario B: cancelled after tool execution — adds cancellation message."""
        session = _make_session([])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-1",
            agent_name="my_agent",
            branch=None,
            temp_text="",
        )

        service.append_event.assert_called_once()
        call_args = service.append_event.call_args
        event_arg = call_args.kwargs.get("event") or call_args[1].get("event", call_args[0][1] if len(call_args[0]) > 1 else None)
        text = event_arg.get_text()
        assert text == _CANCELING_SUFFIX

    async def test_non_streaming_cancellation_cleans_incomplete_calls(self):
        """Scenario B also cleans up incomplete function calls."""
        fc = FunctionCall(id="orphan", name="tool", args={})
        event_with_orphan = _make_event(function_calls=[fc])
        session = _make_session([event_with_orphan])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-1",
            agent_name="agent",
            branch=None,
            temp_text="",
        )

        assert len(session.events[0].get_function_calls()) == 0

    async def test_non_streaming_with_branch(self):
        """Branch is passed through to cancel event."""
        session = _make_session([])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-1",
            agent_name="agent",
            branch="root.sub",
            temp_text="",
        )

        call_args = service.append_event.call_args
        event_arg = call_args.kwargs.get("event") or call_args[1].get("event", call_args[0][1] if len(call_args[0]) > 1 else None)
        assert event_arg.branch == "root.sub"

    async def test_default_temp_text_empty(self):
        """When temp_text defaults, non-streaming path is taken."""
        session = _make_session([])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-1",
            agent_name="agent",
            branch=None,
        )

        call_args = service.append_event.call_args
        event_arg = call_args.kwargs.get("event") or call_args[1].get("event", call_args[0][1] if len(call_args[0]) > 1 else None)
        text = event_arg.get_text()
        assert text == _CANCELING_SUFFIX

    async def test_streaming_text_with_newlines(self):
        """Streaming text with special characters is preserved."""
        session = _make_session([])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-1",
            agent_name="agent",
            branch=None,
            temp_text="line1\nline2\nline3",
        )

        call_args = service.append_event.call_args
        event_arg = call_args.kwargs.get("event") or call_args[1].get("event", call_args[0][1] if len(call_args[0]) > 1 else None)
        text = event_arg.get_text()
        assert "line1\nline2\nline3" in text

    async def test_non_streaming_preserves_complete_calls(self):
        """Complete function call/response pairs are preserved during cleanup."""
        fc = FunctionCall(id="fc1", name="tool", args={})
        fr = FunctionResponse(id="fc1", name="tool", response={"r": 1})
        e1 = _make_event(function_calls=[fc])
        e2 = _make_event(function_responses=[fr])
        session = _make_session([e1, e2])
        service = _make_session_service()

        await handle_cancellation_session_cleanup(
            session=session,
            session_service=service,
            invocation_id="inv-1",
            agent_name="agent",
            branch=None,
            temp_text="",
        )

        assert len(session.events[0].get_function_calls()) == 1
