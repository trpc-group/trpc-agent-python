# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for cancel cleanup and unfinished tool-call completion."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from trpc_agent_sdk.cancel._cleanup import build_cancellation_text
from trpc_agent_sdk.cancel._cleanup import complete_incomplete_function_calls
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


def _event(
    *,
    author: str = "agent",
    invocation_id: str = "inv-1",
    calls: list[FunctionCall] | None = None,
    responses: list[FunctionResponse] | None = None,
    text: str = "",
) -> Event:
    parts = []
    if text:
        parts.append(Part.from_text(text=text))
    for call in calls or []:
        parts.append(Part(function_call=call))
    for response in responses or []:
        parts.append(Part(function_response=response))
    return Event(
        invocation_id=invocation_id,
        author=author,
        content=Content(role="user", parts=parts),
    )


def _session(events: list[Event]):
    session = MagicMock()
    session.events = events
    return session


def _service():
    service = AsyncMock()
    service.append_event = AsyncMock()
    return service


def _appended_event(service: AsyncMock) -> Event:
    return service.append_event.call_args.kwargs["event"]


class TestCompleteIncompleteFunctionCalls:

    async def test_no_calls(self):
        session = _session([])
        service = _service()
        result = await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-1",
            agent_name="agent",
        )
        assert result == []
        service.append_event.assert_not_awaited()

    async def test_complete_pair_is_untouched(self):
        call = FunctionCall(id="fc1", name="tool1", args={})
        response = FunctionResponse(id="fc1", name="tool1", response={"ok": True})
        session = _session([
            _event(calls=[call]),
            _event(responses=[response]),
        ])
        service = _service()

        result = await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-1",
            agent_name="agent",
        )
        assert result == []
        service.append_event.assert_not_awaited()

    async def test_unmatched_call_gets_skipped_response(self):
        call = FunctionCall(id="fc1", name="tool1", args={})
        session = _session([_event(calls=[call])])
        service = _service()

        result = await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-1",
            agent_name="agent",
            branch="root.agent",
        )

        assert [item.id for item in result] == ["fc1"]
        service.append_event.assert_awaited_once()
        event = _appended_event(service)
        assert event.invocation_id == "inv-1"
        assert event.author == "agent"
        assert event.branch == "root.agent"
        assert event.visible is False
        responses = event.get_function_responses()
        assert len(responses) == 1
        assert responses[0].id == "fc1"
        assert responses[0].name == "tool1"
        assert responses[0].response["status"] == "cancelled"
        assert "Tool execution cancelled" in responses[0].response["message"]
        assert "Run cancelled." in responses[0].response["message"]

    async def test_multiple_unmatched_calls_share_one_event(self):
        calls = [
            FunctionCall(id="fc1", name="tool1", args={}),
            FunctionCall(id="fc2", name="tool2", args={}),
        ]
        session = _session([_event(calls=calls)])
        service = _service()

        await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-1",
            agent_name="agent",
        )

        event = _appended_event(service)
        assert [response.id for response in event.get_function_responses()] == ["fc1", "fc2"]

    async def test_only_current_invocation_is_completed(self):
        old_call = FunctionCall(id="old", name="tool", args={})
        new_call = FunctionCall(id="new", name="tool", args={})
        session = _session([
            _event(calls=[old_call], invocation_id="inv-old"),
            _event(calls=[new_call], invocation_id="inv-new"),
        ])
        service = _service()

        result = await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-new",
            agent_name="agent",
        )

        assert [item.id for item in result] == ["new"]

    async def test_response_from_other_invocation_counts(self):
        call = FunctionCall(id="fc1", name="tool", args={})
        response = FunctionResponse(id="fc1", name="tool", response={})
        session = _session([
            _event(calls=[call], invocation_id="inv-1"),
            _event(responses=[response], invocation_id="inv-2"),
        ])
        service = _service()

        result = await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-1",
            agent_name="agent",
        )

        assert result == []
        service.append_event.assert_not_awaited()

    async def test_duplicate_call_id_completed_once(self):
        call = FunctionCall(id="fc1", name="tool", args={})
        session = _session([
            _event(calls=[call]),
            _event(calls=[call]),
        ])
        service = _service()

        result = await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-1",
            agent_name="agent",
        )

        assert [item.id for item in result] == ["fc1"]
        assert len(_appended_event(service).get_function_responses()) == 1

    async def test_custom_reason(self):
        call = FunctionCall(id="fc1", name="tool", args={})
        session = _session([_event(calls=[call])])
        service = _service()

        await complete_incomplete_function_calls(
            session,
            service,
            invocation_id="inv-1",
            agent_name="agent",
            reason="Run limit exceeded.",
        )

        message = _appended_event(service).get_function_responses()[0].response["message"]
        assert "Run limit exceeded." in message


class TestBuildCancellationText:

    def test_without_partial_text(self):
        assert build_cancellation_text() == "Detect user cancel the agent execution."

    def test_with_partial_text(self):
        text = build_cancellation_text("partial answer")

        assert text == "partial answer\n\nDetect user cancel the agent execution."
