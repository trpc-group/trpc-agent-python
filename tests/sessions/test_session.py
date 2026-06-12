# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import time

from google.genai.types import Content
from google.genai.types import Part
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import is_summary_anchor


class TestSession:
    """Test suite for Session class."""

    def test_create_session(self):
        """Test creating a new session."""
        session = Session(id="test-session", app_name="test-app", user_id="test-user", save_key="test-key")

        assert session.id == "test-session"
        assert session.app_name == "test-app"
        assert session.user_id == "test-user"
        assert session.events == []

    def test_add_event(self):
        """Test adding events to a session."""
        session = Session(id="test-session", app_name="test-app", user_id="test-user", save_key="test-key")

        event = Event(author="user", content=Content(parts=[Part.from_text(text="Hello")]))
        session.add_event(event)

        assert len(session.events) == 1
        assert session.events[0].author == "user"
        assert session.last_update_time == event.timestamp

    def test_is_anchor_message(self):
        """Test checking if an event can anchor visible conversation history."""
        user_event = Event(author="user", content=Content(parts=[Part.from_text(text="Hello")]))
        agent_event = Event(author="agent-1", content=Content(parts=[Part.from_text(text="Hi")]))
        summary_event = Event(author="system", content=Content(parts=[Part.from_text(text="Summary")]))
        summary_event.set_summary_event(True)

        assert is_summary_anchor(user_event) is True
        assert is_summary_anchor(agent_event) is False
        assert is_summary_anchor(summary_event) is True

    def test_apply_event_filtering_no_config(self):
        """Test event filtering with no configuration."""
        session = Session(id="test-session", app_name="test-app", user_id="test-user", save_key="test-key")

        # Add some events
        for i in range(10):
            event = Event(author="user" if i % 2 == 0 else "agent",
                          content=Content(parts=[Part.from_text(text=f"Message {i}")]))
            session.events.append(event)

        # No filtering parameters provided
        session.apply_event_filtering()

        # No filtering should occur
        assert len(session.events) == 10

    def test_apply_event_filtering_max_events(self):
        """Test event filtering with max_events limit."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        # Add 10 events (even indices are user messages)
        for i in range(10):
            event = Event(author="user" if i % 2 == 0 else "agent",
                          content=Content(parts=[Part.from_text(text=f"Message {i}")]))
            session.events.append(event)

        # Apply filtering with max_events=5
        session.apply_event_filtering(max_events=5)

        assert len(session.events) == 4
        assert session.events[0].get_text() == "Message 6"
        assert session.events[-1].get_text() == "Message 9"

    def test_apply_event_filtering_can_store_filtered_events(self):
        """Test filtered events can be moved into historical_events."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        for i in range(6):
            event = Event(author="user" if i == 2 else "agent",
                          content=Content(parts=[Part.from_text(text=f"Message {i}")]))
            session.events.append(event)

        session.apply_event_filtering(max_events=2, store_filtered_events=True)

        assert [event.get_text() for event in session.events] == ["Message 2"]
        assert [event.get_text() for event in session.historical_events] == [
            "Message 0",
            "Message 1",
            "Message 3",
            "Message 4",
            "Message 5",
        ]
        assert all(event.is_model_visible() for event in session.historical_events)

    def test_apply_event_filtering_ttl(self):
        """Test event filtering with TTL."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        current_time = time.time()

        # Add old user events (3 seconds ago)
        for i in range(2):
            event = Event(author="user", content=Content(parts=[Part.from_text(text=f"Old user message {i}")]))
            event.timestamp = current_time - 3.0
            session.events.append(event)

        # Add old agent event
        event = Event(author="agent", content=Content(parts=[Part.from_text(text="Old agent message")]))
        event.timestamp = current_time - 3.0
        session.events.append(event)

        # Add recent events (1 second ago) - all agent messages
        for i in range(3):
            event = Event(author="agent", content=Content(parts=[Part.from_text(text=f"Recent message {i}")]))
            event.timestamp = current_time - 1.0
            session.events.append(event)

        # Apply TTL filtering with 2 seconds
        session.apply_event_filtering(event_ttl_seconds=2.0)

        # TTL + user-anchor fallback keeps only the last user message.
        assert len(session.events) == 1
        assert session.events[0].author == "user"
        assert "Old user message 1" in session.events[0].get_text()

    def test_apply_event_filtering_ttl_and_max_events(self):
        """Test event filtering with both TTL and max_events."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        current_time = time.time()

        # Add 10 old user events (6 seconds ago) - should be filtered by TTL
        for i in range(10):
            event = Event(author="user", content=Content(parts=[Part.from_text(text=f"Very old {i}")]))
            event.timestamp = current_time - 6.0
            session.events.append(event)

        # Add 10 recent events (1 second ago) - should pass TTL but be limited by max_events
        # Mix user and agent messages
        for i in range(10):
            author = "user" if i % 3 == 0 else "agent"
            event = Event(author=author, content=Content(parts=[Part.from_text(text=f"Recent {i}")]))
            event.timestamp = current_time - 1.0
            session.events.append(event)

        # Apply both filters
        session.apply_event_filtering(event_ttl_seconds=5.0, max_events=5)

        assert len(session.events) == 4
        assert session.events[0].get_text() == "Recent 6"
        assert session.events[-1].get_text() == "Recent 9"

    def test_apply_event_filtering_preserves_last_user_message(self):
        """Test that filtering preserves the last user message when all events are filtered."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        current_time = time.time()

        # Add old events
        user_event = Event(author="user", content=Content(parts=[Part.from_text(text="User question")]))
        user_event.timestamp = current_time - 10.0
        session.events.append(user_event)

        for i in range(5):
            agent_event = Event(author="agent", content=Content(parts=[Part.from_text(text=f"Agent response {i}")]))
            agent_event.timestamp = current_time - 10.0
            session.events.append(agent_event)

        # Another user event
        last_user_event = Event(author="user", content=Content(parts=[Part.from_text(text="Last user message")]))
        last_user_event.timestamp = current_time - 10.0
        session.events.append(last_user_event)

        # Apply strict TTL filter that would remove all events
        session.apply_event_filtering(event_ttl_seconds=2.0)

        # All events are old, but last user message remains as the anchor.
        assert len(session.events) == 1
        assert session.events[0].author == "user"
        assert session.events[0].get_text() == "Last user message"

    def test_apply_event_filtering_empty_events(self):
        """Test event filtering with no events."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        session.apply_event_filtering(event_ttl_seconds=2.0, max_events=5)

        assert session.events == []

    def test_apply_event_filtering_all_filtered_no_user_message(self):
        """Test filtering when all events are filtered and there's no user message."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        current_time = time.time()

        # Add only agent events (old)
        for i in range(5):
            agent_event = Event(author="agent", content=Content(parts=[Part.from_text(text=f"Agent message {i}")]))
            agent_event.timestamp = current_time - 10.0
            session.events.append(agent_event)

        # Apply strict TTL filter
        session.apply_event_filtering(event_ttl_seconds=2.0)

        assert session.events == []

    def test_apply_event_filtering_case_insensitive_user(self):
        """Test that user detection is case-insensitive."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        current_time = time.time()

        # Add events with different case for "user"
        for author in ["USER", "User", "user", "uSeR"]:
            event = Event(author=author, content=Content(parts=[Part.from_text(text=f"Message from {author}")]))
            event.timestamp = current_time - 10.0
            session.events.append(event)

        # Add agent event
        agent_event = Event(author="agent", content=Content(parts=[Part.from_text(text="Agent message")]))
        agent_event.timestamp = current_time - 10.0
        session.events.append(agent_event)

        # Apply strict TTL filter
        session.apply_event_filtering(event_ttl_seconds=2.0)

        # Last user message is preserved as the anchor (case-insensitive).
        assert len(session.events) == 1
        assert session.events[0].author.lower() == "user"
        assert session.events[0].get_text() == "Message from uSeR"

    def test_apply_event_filtering_max_events_less_than_one(self):
        """Test that max_events <= 0 is treated as no limit."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        # Add events
        for i in range(10):
            event = Event(author="user", content=Content(parts=[Part.from_text(text=f"Message {i}")]))
            session.events.append(event)

        # Apply filtering with max_events=0 (no limit)
        session.apply_event_filtering(max_events=0)

        # Should keep all events
        assert len(session.events) == 10

    def test_apply_event_filtering_ttl_less_than_zero(self):
        """Test that TTL <= 0 is treated as no TTL."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        current_time = time.time()

        # Add very old events
        for i in range(5):
            event = Event(author="user", content=Content(parts=[Part.from_text(text=f"Old message {i}")]))
            event.timestamp = current_time - 100.0
            session.events.append(event)

        # Apply filtering with event_ttl_seconds=0 (no TTL)
        session.apply_event_filtering(event_ttl_seconds=0)

        # Should keep all events
        assert len(session.events) == 5

    def test_add_event_with_filtering(self):
        """Test add_event with filtering parameters."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        # Add 10 events with max_events=5 (even indices are user messages)
        for i in range(10):
            event = Event(author="user" if i % 2 == 0 else "agent",
                          content=Content(parts=[Part.from_text(text=f"Message {i}")]))
            session.add_event(event, max_events=5)

        assert len(session.events) == 4
        assert session.events[0].get_text() == "Message 6"
        assert session.events[-1].get_text() == "Message 9"

    def test_add_event_with_filtering_can_store_filtered_events(self):
        """Test add_event moves filtered events to historical_events when requested."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        for i in range(6):
            event = Event(author="user" if i == 2 else "agent",
                          content=Content(parts=[Part.from_text(text=f"Message {i}")]))
            session.add_event(event, max_events=2, store_filtered_events=True)

        assert [event.get_text() for event in session.events] == ["Message 2", "Message 5"]
        assert [event.get_text() for event in session.historical_events] == [
            "Message 0",
            "Message 1",
            "Message 3",
            "Message 4",
        ]

    def test_apply_event_filtering_keeps_first_user_message_and_after(self):
        """Test that filtering keeps the first user message and all events after it."""
        session = Session(
            id="test-session",
            app_name="test-app",
            user_id="test-user",
            save_key="test-key",
        )

        current_time = time.time()

        # Add agent events before user message
        for i in range(3):
            event = Event(author="agent", content=Content(parts=[Part.from_text(text=f"Agent {i}")]))
            event.timestamp = current_time - 1.0
            session.events.append(event)

        # Add user message
        user_event = Event(author="user", content=Content(parts=[Part.from_text(text="User question")]))
        user_event.timestamp = current_time - 1.0
        session.events.append(user_event)

        # Add more agent events after user message
        for i in range(3):
            event = Event(author="agent", content=Content(parts=[Part.from_text(text=f"Agent response {i}")]))
            event.timestamp = current_time - 1.0
            session.events.append(event)

        # Apply max_events filter with small limit
        session.apply_event_filtering(max_events=3)

        # When the retained tail has no user message, fallback keeps the last user message only.
        assert len(session.events) == 1
        assert session.events[0].author == "user"
        assert session.events[0].get_text() == "User question"
