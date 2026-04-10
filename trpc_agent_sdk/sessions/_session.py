# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session data structure."""

from __future__ import annotations

import time
from typing import List

from pydantic import Field
from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.events import Event


class Session(SessionABC):
    """Represents a series of interactions between a user and agents.

    This class manages the state and events of a conversation session,
    providing methods to add events, update state, and track session metadata.

    Attributes:
        id: The unique identifier of the session.
        app_name: The name of the application.
        user_id: The id of the user.
        state: The state of the session as a dictionary.
        events: The events of the session, e.g. user input, model response,
                function call/response, etc.
        last_update_time: The last update time as a float timestamp.
    """
    events: List[Event] = Field(default_factory=list, description="The events of the session")
    """The events of the session, e.g. user input, model response, function call/response, etc."""

    def add_event(self, event: Event, event_ttl_seconds: float = 0.0, max_events: int = 0) -> None:
        """Add an event to the session and update the last update time.

        Args:
            event: The event to add to the session.
            event_ttl_seconds: Time-to-live in seconds for events. If 0, no TTL filtering is applied.
            max_events: Maximum number of events to keep. If 0, no limit is applied.
        """
        self.events.append(event)
        self.apply_event_filtering(event_ttl_seconds, max_events)
        self.last_update_time = event.timestamp

    def apply_event_filtering(self, event_ttl_seconds: float = 0.0, max_events: int = 0) -> None:
        """Apply event filtering based on TTL and maximum event count.

        This method filters events in two steps:
        1. TTL filtering: Remove events older than event_ttl_seconds from now
        2. Count filtering: Keep only the most recent max_events events

        If both filters result in removing all events, the method attempts to
        preserve the first user message and all events after it from the original events.

        Args:
            event_ttl_seconds: Time-to-live in seconds for events. If 0, no TTL filtering is applied.
            max_events: Maximum number of events to keep. If 0, no limit is applied.
        """
        if not self.events:
            return

        # If neither filter is configured, return early
        if event_ttl_seconds <= 0 and max_events <= 0:
            return

        # Save original events for potential user message recovery
        original_events = self.events.copy()

        # Step 1: Apply TTL filtering if configured
        if event_ttl_seconds > 0:
            cutoff_time = time.time() - event_ttl_seconds
            self.events = [e for e in self.events if e.timestamp >= cutoff_time]

        # Step 2: Apply count filtering if configured
        if max_events > 0:
            if len(self.events) > max_events:
                self.events = self.events[-max_events:]

        for i, event in enumerate(self.events):
            if self._is_user_message(event):
                self.events = self.events[i:]
                return

        # Step 3: If all events were filtered out, insert the first user message at the beginning
        # Find the last user message from original events
        for event in reversed(original_events):
            if self._is_user_message(event):
                self.events.insert(0, event)
                return

        # If no user message found, keep events empty
        self.events = []

    def _is_user_message(self, event: Event) -> bool:
        """Check if an event is a user message.

        Args:
            event: The event to check.

        Returns:
            True if the event is from a user, False otherwise.
        """
        return event.author.lower() == "user"
