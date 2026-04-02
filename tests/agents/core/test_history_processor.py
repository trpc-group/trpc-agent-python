# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for HistoryProcessor."""

from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import Mock

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents.core._history_processor import (
    BranchFilterMode,
    HistoryProcessor,
    TimelineFilterMode,
    _TRPC_USER_MESSAGE_BRANCH,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield


def _make_event(
    author: str,
    text: str = "",
    branch: str = None,
    invocation_id: str = "inv-1",
    function_call: FunctionCall = None,
    function_response: FunctionResponse = None,
) -> Event:
    parts = []
    if text:
        parts.append(Part(text=text))
    if function_call:
        parts.append(Part(function_call=function_call))
    if function_response:
        parts.append(Part(function_response=function_response))

    return Event(
        invocation_id=invocation_id,
        author=author,
        branch=branch,
        content=Content(parts=parts) if parts else None,
    )


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = _StubAgent(name="test_agent")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="coordinator.math_agent",
    )
    return ctx


# ---------------------------------------------------------------------------
# TimelineFilterMode / BranchFilterMode enums
# ---------------------------------------------------------------------------


class TestTimelineFilterMode:
    def test_all_value(self):
        assert TimelineFilterMode.ALL == "all"

    def test_invocation_value(self):
        assert TimelineFilterMode.INVOCATION == "invocation"


class TestBranchFilterMode:
    def test_all_value(self):
        assert BranchFilterMode.ALL == "all"

    def test_prefix_value(self):
        assert BranchFilterMode.PREFIX == "prefix"

    def test_exact_value(self):
        assert BranchFilterMode.EXACT == "exact"


# ---------------------------------------------------------------------------
# HistoryProcessor.filter_events - Timeline filtering
# ---------------------------------------------------------------------------


class TestTimelineFiltering:
    def test_all_mode_includes_all(self, invocation_context):
        proc = HistoryProcessor(timeline_filter_mode=TimelineFilterMode.ALL)
        e1 = _make_event("user", "hi", invocation_id="inv-old")
        e2 = _make_event("agent", "hello", invocation_id="inv-1", branch="coordinator.math_agent")
        events = proc.filter_events(invocation_context, [e1, e2])
        assert len(events) == 2

    def test_invocation_mode_filters_by_id(self, invocation_context):
        proc = HistoryProcessor(timeline_filter_mode=TimelineFilterMode.INVOCATION)
        e1 = _make_event("user", "old", invocation_id="inv-old")
        e2 = _make_event("user", "current", invocation_id="inv-1")
        events = proc.filter_events(invocation_context, [e1, e2])
        assert len(events) == 1
        assert events[0].content.parts[0].text == "current"


# ---------------------------------------------------------------------------
# HistoryProcessor.filter_events - Branch filtering
# ---------------------------------------------------------------------------


class TestBranchFiltering:
    def test_all_mode_includes_all_branches(self, invocation_context):
        proc = HistoryProcessor(branch_filter_mode=BranchFilterMode.ALL)
        e1 = _make_event("a", "1", branch="coordinator.math_agent")
        e2 = _make_event("b", "2", branch="coordinator.other_agent")
        events = proc.filter_events(invocation_context, [e1, e2])
        assert len(events) == 2

    def test_exact_mode_filters_different_branches(self, invocation_context):
        proc = HistoryProcessor(branch_filter_mode=BranchFilterMode.EXACT)
        e1 = _make_event("a", "1", branch="coordinator.math_agent")
        e2 = _make_event("b", "2", branch="coordinator.other_agent")
        events = proc.filter_events(invocation_context, [e1, e2])
        assert len(events) == 1
        assert events[0].branch == "coordinator.math_agent"

    def test_prefix_mode_includes_ancestors(self, invocation_context):
        proc = HistoryProcessor(branch_filter_mode=BranchFilterMode.PREFIX)
        e1 = _make_event("a", "1", branch="coordinator")
        e2 = _make_event("b", "2", branch="coordinator.math_agent")
        e3 = _make_event("c", "3", branch="coordinator.info_agent")
        events = proc.filter_events(invocation_context, [e1, e2, e3])
        assert len(events) == 2
        branches = {e.branch for e in events}
        assert "coordinator" in branches
        assert "coordinator.math_agent" in branches

    def test_prefix_mode_excludes_siblings(self, invocation_context):
        proc = HistoryProcessor(branch_filter_mode=BranchFilterMode.PREFIX)
        e = _make_event("a", "1", branch="coordinator.info_agent")
        events = proc.filter_events(invocation_context, [e])
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Content filtering
# ---------------------------------------------------------------------------


class TestContentFiltering:
    def test_events_without_content_excluded(self, invocation_context):
        proc = HistoryProcessor()
        e = Event(invocation_id="inv-1", author="agent", content=None)
        events = proc.filter_events(invocation_context, [e])
        assert len(events) == 0

    def test_events_with_empty_parts_excluded(self, invocation_context):
        proc = HistoryProcessor()
        e = Event(invocation_id="inv-1", author="agent", content=Content(parts=[]))
        events = proc.filter_events(invocation_context, [e])
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Transfer-to-agent filtering
# ---------------------------------------------------------------------------


class TestTransferToAgentFiltering:
    def test_transfer_function_call_excluded(self, invocation_context):
        proc = HistoryProcessor()
        fc = FunctionCall(name="transfer_to_agent", args={"agent_name": "other"})
        e = _make_event("agent", function_call=fc)
        events = proc.filter_events(invocation_context, [e])
        assert len(events) == 0

    def test_transfer_function_response_excluded(self, invocation_context):
        proc = HistoryProcessor()
        fr = FunctionResponse(name="transfer_to_agent", response={"result": "ok"})
        e = _make_event("agent", function_response=fr)
        events = proc.filter_events(invocation_context, [e])
        assert len(events) == 0

    def test_non_transfer_function_call_included(self, invocation_context):
        proc = HistoryProcessor()
        fc = FunctionCall(name="my_tool", args={"key": "val"})
        e = _make_event("agent", function_call=fc)
        events = proc.filter_events(invocation_context, [e])
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Max history messages
# ---------------------------------------------------------------------------


class TestMaxHistoryMessages:
    def test_no_limit(self, invocation_context):
        proc = HistoryProcessor(max_history_messages=0)
        events_in = [_make_event("user", f"msg {i}") for i in range(10)]
        events = proc.filter_events(invocation_context, events_in)
        assert len(events) == 10

    def test_limit_applied(self, invocation_context):
        proc = HistoryProcessor(max_history_messages=3)
        events_in = [_make_event("user", f"msg {i}") for i in range(10)]
        events = proc.filter_events(invocation_context, events_in)
        assert len(events) == 3

    def test_limit_keeps_last_n(self, invocation_context):
        proc = HistoryProcessor(max_history_messages=2)
        events_in = [_make_event("user", f"msg {i}") for i in range(5)]
        events = proc.filter_events(invocation_context, events_in)
        assert len(events) == 2
        assert events[0].content.parts[0].text == "msg 3"
        assert events[1].content.parts[0].text == "msg 4"


# ---------------------------------------------------------------------------
# User event branch tagging
# ---------------------------------------------------------------------------


class TestUserEventBranchTagging:
    def test_user_events_tagged_in_prefix_mode(self, invocation_context):
        proc = HistoryProcessor(branch_filter_mode=BranchFilterMode.PREFIX)
        e1 = _make_event("user", "hello")
        e2 = _make_event("coordinator", "response", branch="coordinator")
        e3 = _make_event("user", "next")
        events = proc.filter_events(invocation_context, [e1, e2, e3])
        # Last user event always included (no tagging for last)
        # First user event tagged based on agent that replied
        assert any(e.content.parts[0].text == "next" for e in events)

    def test_cleanup_removes_calculated_branch(self, invocation_context):
        proc = HistoryProcessor(branch_filter_mode=BranchFilterMode.EXACT)
        e1 = _make_event("user", "hello")
        e2 = _make_event("agent", "hi", branch="coordinator.math_agent")
        e3 = _make_event("user", "bye")
        events = proc.filter_events(invocation_context, [e1, e2, e3])
        for e in events:
            if e.custom_metadata:
                assert _TRPC_USER_MESSAGE_BRANCH not in e.custom_metadata
