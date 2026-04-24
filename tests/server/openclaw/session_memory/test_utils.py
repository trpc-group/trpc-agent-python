"""Unit tests for trpc_agent_sdk.server.openclaw.session_memory._utils."""

from __future__ import annotations

import time

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.server.openclaw.session_memory._utils import event_to_openai_message
from trpc_agent_sdk.server.openclaw.session_memory._utils import get_messages_from_session
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content, Part


def _make_session(events: list[Event], session_id: str = "s1") -> Session:
    session = Session(id=session_id, app_name="app", user_id="user", save_key=f"app/user/{session_id}")
    session.events = events
    return session


def test_event_to_openai_message_handles_none_content() -> None:
    event = Event(
        invocation_id="inv-1",
        author="user",
        content=None,
        timestamp=time.time(),
    )

    msg = event_to_openai_message(event)

    assert msg is None


def test_get_messages_from_session_handles_none_content_event() -> None:
    events = [
        Event(invocation_id="inv-1", author="user", content=None, timestamp=time.time()),
        Event(
            invocation_id="inv-2",
            author="assistant",
            content=Content(role="assistant", parts=[Part.from_text(text="ok")]),
            timestamp=time.time(),
        ),
    ]
    session = _make_session(events)

    messages = get_messages_from_session(session)

    assert len(messages) == 1
    assert messages[0]["content"] == "ok"
