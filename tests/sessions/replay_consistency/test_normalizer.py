# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Unit tests for the replay consistency normalizer."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.types import Content, EventActions, Part


def _make_event(
    author: str,
    text: str = "",
    invocation_id: str = "inv-001",
    state_delta: dict[str, str] | None = None,
) -> Event:
    actions = EventActions(state_delta=state_delta) if state_delta else EventActions()
    content = Content(parts=[Part.from_text(text=text)] if text else [])
    return Event(
        invocation_id=invocation_id,
        author=author,
        content=content,
        actions=actions,
    )


# ---------------------------------------------------------------------------
# RED phase — these tests fail until normalizer is implemented
# ---------------------------------------------------------------------------


class TestNormalizeEvent:
    """Tests for normalize_event()."""

    def test_preserves_author(self):
        from tests.sessions.replay_consistency.normalizer import normalize_event
        event = _make_event(author="user", text="Hello")
        norm = normalize_event(event)
        assert norm["author"] == "user"

    def test_extracts_text_from_parts(self):
        from tests.sessions.replay_consistency.normalizer import normalize_event
        content = Content(parts=[
            Part.from_text(text="Hello"),
            Part.from_text(text="world!"),
        ])
        event = Event(
            invocation_id="inv-001",
            author="assistant",
            content=content,
            actions=EventActions(),
        )
        norm = normalize_event(event)
        assert norm["text"] == "Hello world!"

    def test_handles_empty_parts(self):
        from tests.sessions.replay_consistency.normalizer import normalize_event
        event = Event(
            invocation_id="inv-001",
            author="user",
            content=Content(parts=[]),
            actions=EventActions(),
        )
        norm = normalize_event(event)
        assert norm.get("text", "") == ""

    def test_handles_content_without_text(self):
        from tests.sessions.replay_consistency.normalizer import normalize_event
        content = Content(parts=[Part.from_text(text="")])
        event = Event(
            invocation_id="inv-001",
            author="assistant",
            content=content,
            actions=EventActions(),
        )
        norm = normalize_event(event)
        assert "text" in norm
        assert norm["text"] == ""

    def test_includes_state_delta(self):
        from tests.sessions.replay_consistency.normalizer import normalize_event
        event = _make_event(
            author="assistant",
            text="Updated.",
            state_delta={"user:score": "10"},
        )
        norm = normalize_event(event)
        assert "state_delta" in norm
        assert norm["state_delta"] == {"user:score": "10"}

    def test_no_state_delta_when_absent(self):
        from tests.sessions.replay_consistency.normalizer import normalize_event
        event = _make_event(author="user", text="Hi")
        norm = normalize_event(event)
        assert "state_delta" not in norm

    def test_multi_part_tool_call_event(self):
        from tests.sessions.replay_consistency.normalizer import normalize_event
        content = Content(parts=[Part.from_text(text="Calling tool...")])
        event = Event(
            invocation_id="inv-002",
            author="assistant",
            content=content,
            actions=EventActions(),
        )
        norm = normalize_event(event)
        assert norm["author"] == "assistant"
        assert "Calling tool..." in norm["text"]


class TestNormalizeSnapshot:
    """Tests for normalize_snapshot()."""

    def test_includes_session_id(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        session = Session(
            id="session-001", app_name="test-app", user_id="user-1",
            state={}, events=[], save_key="test-key",
        )
        snap = normalize_snapshot(session, [])
        assert snap["session_id"] == "session-001"

    def test_includes_state_as_dict(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        session = Session(
            id="session-001", app_name="test-app", user_id="user-1",
            state={"key": "value"}, events=[], save_key="test-key",
        )
        snap = normalize_snapshot(session, [])
        assert snap["state"] == {"key": "value"}

    def test_empty_state_becomes_empty_dict(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        session = Session(
            id="session-001", app_name="test-app", user_id="user-1",
            state={}, events=[], save_key="test-key",
        )
        snap = normalize_snapshot(session, [])
        assert snap["state"] == {}

    def test_normalizes_multiple_events(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        events = [
            _make_event(author="user", text="Q1", invocation_id="inv-1"),
            _make_event(author="assistant", text="A1", invocation_id="inv-2"),
            _make_event(author="user", text="Q2", invocation_id="inv-3"),
        ]
        session = Session(
            id="session-002", app_name="test-app", user_id="user-1",
            state={}, events=events, save_key="test-key",
        )
        snap = normalize_snapshot(session, [])
        assert len(snap["events"]) == 3
        assert snap["events"][0]["author"] == "user"
        assert snap["events"][0]["text"] == "Q1"
        assert snap["events"][1]["text"] == "A1"

    def test_sorts_memories_by_content(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        session = Session(
            id="session-003", app_name="test-app", user_id="user-1",
            state={}, events=[], save_key="test-key",
        )
        memories = [
            {"content": "Zebra", "topics": ["animals"]},
            {"content": "Apple", "topics": ["fruit"]},
            {"content": "Mango", "topics": ["fruit"]},
        ]
        snap = normalize_snapshot(session, memories)
        sorted_contents = [m["content"] for m in snap["memories"]]
        assert sorted_contents == ["Apple", "Mango", "Zebra"]

    def test_includes_summaries(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        session = Session(
            id="session-004", app_name="test-app", user_id="user-1",
            state={}, events=[], save_key="test-key",
        )
        snap = normalize_snapshot(session, [])
        summaries = snap.get("summaries", {})
        assert isinstance(summaries, dict)

    def test_unicode_text_preserved(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        events = [
            _make_event(author="user", text="你好世界 \U0001f680测试", invocation_id="inv-1"),
        ]
        session = Session(
            id="session-u", app_name="test-app", user_id="user-1",
            state={}, events=events, save_key="test-key",
        )
        snap = normalize_snapshot(session, [])
        assert "你好世界 \U0001f680测试" in snap["events"][0]["text"]

    def test_empty_session_events(self):
        from tests.sessions.replay_consistency.normalizer import normalize_snapshot
        session = Session(
            id="session-empty", app_name="test-app", user_id="user-1",
            state={}, events=[], save_key="test-key",
        )
        snap = normalize_snapshot(session, [])
        assert snap["events"] == []
        assert snap["memories"] == []
