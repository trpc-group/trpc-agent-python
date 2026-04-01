# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Directly reuse the types from adk-python
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Base session service interface."""

from __future__ import annotations

import time
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import State

from ._session import Session
from ._summarizer_manager import SummarizerSessionManager
from ._types import SessionServiceConfig


class BaseSessionService(SessionServiceABC):
    """Abstract base class for session management services.

    The service provides a set of methods for managing sessions and events.
    """

    def __init__(self,
                 summarizer_manager: Optional[SummarizerSessionManager] = None,
                 session_config: Optional[SessionServiceConfig] = None):
        """Initialize the base session service.

        Args:
            summarizer_manager: Optional summarizer manager for session summarization
            session_config: Optional session configuration
        """
        self._summarizer_manager = summarizer_manager
        if session_config is None:
            session_config = SessionServiceConfig()
            # Clean up the TTL configuration if not set
            session_config.clean_ttl_config()
        self._session_config = session_config
        if self._summarizer_manager:
            self._summarizer_manager.set_session_service(self)

    @property
    def summarizer_manager(self) -> Optional[SummarizerSessionManager]:
        """Get the summarizer manager."""
        return self._summarizer_manager

    def set_summarizer_manager(self, summarizer_manager: SummarizerSessionManager, force: bool = False) -> None:
        """Set the summarizer manager to use.

        Args:
            summarizer_manager: The summarizer manager to use
            force: Whether to force update even if already set
        """
        if not self._summarizer_manager or force:
            self._summarizer_manager = summarizer_manager
            self._summarizer_manager.set_session_service(self)

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        """Appends an event to a session object."""
        if event.partial:
            return event
        event = self._trim_temp_delta_state(event)
        self.__update_session_state(session, event)
        session.add_event(event,
                          event_ttl_seconds=self._session_config.event_ttl_seconds,
                          max_events=self._session_config.max_events)
        return event

    def _trim_temp_delta_state(self, event: Event) -> Event:
        """Removes temporary state delta keys from the event."""
        if not event.actions or not event.actions.state_delta:
            return event

        event.actions.state_delta = {
            key: value
            for key, value in event.actions.state_delta.items() if not key.startswith(State.TEMP_PREFIX)
        }
        return event

    def __update_session_state(self, session: Session, event: Event) -> None:
        """Updates the session state based on the event.

        Applies state changes from event.actions.state_delta to the session.
        Handles different state prefixes:
        - No prefix: Session-scoped state (stored in session.state)
        - 'user:': User-scoped state (managed by SessionService implementation)
        - 'app:': Application-scoped state (managed by SessionService implementation)
        - 'temp:': Temporary state (never persisted, skipped)

        Args:
            session: The session to update
            event: The event containing state changes
        """
        if not event.actions or not event.actions.state_delta:
            return

        for key, value in event.actions.state_delta.items():
            if key.startswith(State.TEMP_PREFIX):
                # Skip temporary state - never persisted
                continue
            # Session-scoped state
            session.state[key] = value

    async def update_session(self, session: Session) -> None:
        """Update a session in storage.

        This method should be implemented by concrete session services
        to persist session changes to their storage backend.

        Args:
            session: The session to update
        """
        # Default implementation does nothing
        # Concrete implementations should override this method
        pass

    @override
    async def create_session_summary(self, session: Session, ctx: Optional[InvocationContext] = None) -> None:
        """Summarize a session.

        Args:
            session: The session to summarize
            ctx: The invocation context
        """
        if self._summarizer_manager:
            await self._summarizer_manager.create_session_summary(session, ctx=ctx)

    @override
    async def get_session_summary(self, session: Session) -> Optional[str]:
        """Get a summary of a session.

        Args:
            session: The session to summarize

        Returns:
            Summary text if available, None otherwise
        """
        if self._summarizer_manager:
            summary = await self._summarizer_manager.get_session_summary(session)
            if summary:
                return summary.summary_text
        return None

    def filter_events(self, session: Session) -> None:
        """Filter events based on the session config."""
        if self._session_config.num_recent_events > 0:
            session.events = session.events[-self._session_config.num_recent_events:]
        if self._session_config.event_ttl_seconds > 0:
            cutoff_timestamp = time.time() - self._session_config.event_ttl_seconds
            i = len(session.events) - 1
            while i >= 0:
                if session.events[i].timestamp <= cutoff_timestamp:
                    break
                i -= 1
            if i >= 0:
                session.events = session.events[i + 1:]

    @override
    async def close(self) -> None:
        """Closes the session service and releases any resources."""
        pass
