# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
"""In-memory session service implementation."""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from typing import Any
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.abc import ListSessionsResponse
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Ttl
from trpc_agent_sdk.utils import user_key

from ._base_session_service import BaseSessionService
from ._session import Session
from ._summarizer_manager import SummarizerSessionManager
from ._types import SessionServiceConfig
from ._utils import StateStorageEntry
from ._utils import extract_state_delta
from ._utils import merge_state


class SessionWithTTL(BaseModel):
    """Wrapper for session with TTL support."""
    session: Session = Field(..., description="Session")
    """Session"""
    ttl: Ttl = Field(default_factory=Ttl, description="TTL configuration")
    """TTL configuration"""

    def update(self, data: Session) -> None:
        """Update the session with a data and a TTL."""
        self.session = data
        self.ttl.update_expired_at()

    def get(self) -> Session:
        """Get the session."""
        if self.ttl.is_expired():
            logger.debug("Session is expired")
            self.session = None
        self.ttl.update_expired_at()
        return self.session


class StateWithTTL(BaseModel):
    """Wrapper for state with TTL support."""
    data: dict[str, Any] = Field(default_factory=dict, description="Dictionary of state data")
    """Dictionary of state data"""
    ttl: Ttl = Field(default_factory=Ttl, description="TTL configuration")
    """TTL configuration"""

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update the state with a data and a TTL."""
        if self.ttl.is_expired():
            logger.debug("State is expired")
            self.data = {}
        self.data.update(data)
        self.ttl.update_expired_at()
        return self.data

    def get(self) -> dict[str, Any]:
        """Get the state."""
        if self.ttl.is_expired():
            logger.debug("State is expired")
            self.data = {}
        self.ttl.update_expired_at()
        return self.data


class InMemorySessionService(BaseSessionService):
    """An in-memory implementation of the session service.

    This service stores sessions in memory with optional TTL and automatic cleanup.
    It is suitable for development and testing, or for production with proper
    TTL configuration to prevent memory growth.
    """

    def __init__(self,
                 summarizer_manager: Optional[SummarizerSessionManager] = None,
                 session_config: Optional[SessionServiceConfig] = None):
        super().__init__(summarizer_manager=summarizer_manager, session_config=session_config)
        # Storage with TTL support
        # Map: app_name -> user_id -> session_id -> SessionWithTTL
        self._sessions: dict[str, dict[str, dict[str, SessionWithTTL]]] = {}
        # Map: app_name -> user_id -> StateWithTTL
        self._user_state: dict[str, dict[str, StateWithTTL]] = {}
        # Map: app_name -> StateWithTTL
        self._app_state: dict[str, StateWithTTL] = {}

        # Cleanup task
        self.__cleanup_task: Optional[asyncio.Task] = None
        self.__cleanup_stop_event: Optional[asyncio.Event] = None

        # Start cleanup task if enabled
        self._start_cleanup_task()

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_context: Optional[AgentContext] = None,
    ) -> Session:
        state_deltas = extract_state_delta(state)

        # Ensure app and user states exist
        app_state = self._update_app_state(app_name, state_deltas.app_state_delta)
        user_state = self._update_user_state(app_name, user_id, state_deltas.user_state_delta)

        # Create session with session-scoped state only
        session_id = session_id.strip() if session_id and session_id.strip() else str(uuid.uuid4())
        session = Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=state_deltas.session_state,
            last_update_time=time.time(),
            save_key=user_key(app_name, user_id),
        )

        # Save session to storage
        self._set_session(app_name, user_id, session_id, session)

        copied_session = copy.deepcopy(session)
        return self._merge_state(app_state, user_state, copied_session)

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: Optional[AgentContext] = None,
    ) -> Optional[Session]:
        if not self._is_session_exist(app_name, user_id, session_id):
            logger.debug("Session %s not found", session_id)
            return None

        session = self._get_session(app_name, user_id, session_id)
        if session is None:
            return None

        copied_session = copy.deepcopy(session)
        self.filter_events(copied_session)

        app_state = self._get_app_state(app_name)
        user_state = self._get_user_state(app_name, user_id)

        return self._merge_state(app_state, user_state, copied_session)

    @override
    async def list_sessions(self, *, app_name: str, user_id: str) -> ListSessionsResponse:
        empty_response = ListSessionsResponse()
        if app_name not in self._sessions:
            return empty_response
        if user_id not in self._sessions[app_name]:
            return empty_response

        sessions_without_events = []
        for session_id in self._sessions[app_name][user_id].keys():
            session = self._get_session(app_name, user_id, session_id)
            if session is None:
                continue

            copied_session = copy.deepcopy(session)
            copied_session.events = []
            app_state = self._get_app_state(app_name)
            user_state = self._get_user_state(app_name, user_id)
            copied_session = self._merge_state(app_state, user_state, copied_session)
            sessions_without_events.append(copied_session)
        return ListSessionsResponse(sessions=sessions_without_events)

    @override
    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        if not self._is_session_exist(app_name=app_name, user_id=user_id, session_id=session_id):
            return
        del self._sessions[app_name][user_id][session_id]

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        # Update the in-memory session.
        if event.partial:
            return event

        # Update the storage session
        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id

        def _warning(message: str) -> None:
            logger.warning("Failed to append event to session %s: %s", session_id, message)

        if app_name not in self._sessions:
            _warning(f"app_name {app_name} not in sessions")
            return event
        if user_id not in self._sessions[app_name]:
            _warning(f"user_id {user_id} not in sessions[app_name]")
            return event
        if session_id not in self._sessions[app_name][user_id]:
            _warning(f"session_id {session_id} not in sessions[app_name][user_id]")
            return event

        await super().append_event(session=session, event=event)

        # Get session with TTL wrapper
        storage_session = self._get_session(app_name, user_id, session_id)
        if storage_session is None:
            _warning("session not found")
            return event

        # Add event to storage session
        storage_session.events.append(event)

        # Extract and apply state changes to appropriate storage buckets
        if event.actions and event.actions.state_delta:
            state_delta = extract_state_delta(event.actions.state_delta)

            # Update app state
            if state_delta.app_state_delta:
                self._update_app_state(app_name, state_delta.app_state_delta)

            # Update user state
            if state_delta.user_state_delta:
                self._update_user_state(app_name, user_id, state_delta.user_state_delta)
            if state_delta.session_state:
                storage_session.state.update(state_delta.session_state)

        storage_session.conversation_count = session.conversation_count

        return event

    @override
    async def update_session(self, session: Session) -> None:
        """Update a session in storage.

        Args:
            session: The session to update
        """
        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id

        if app_name not in self._sessions:
            logger.warning("app_name %s not in sessions", app_name)
            return
        if user_id not in self._sessions[app_name]:
            logger.warning("user_id %s not in sessions[app_name]", user_id)
            return
        if session_id not in self._sessions[app_name][user_id]:
            logger.warning("session_id %s not in sessions[app_name][user_id]", session_id)
            return

        # Update the stored session and refresh TTL
        self._set_session(app_name, user_id, session_id, session)

    def _cleanup_expired(self) -> None:
        """Remove all expired sessions and states.

        Uses two-phase deletion to avoid modifying dictionaries during iteration.
        """
        # Phase 1: Collect expired items
        now = time.time()
        expired_sessions: dict[str, dict[str, list[str]]] = {}
        expired_user_states: dict[str, list[str]] = {}
        expired_app_states: list[str] = []

        # Collect expired sessions
        for app_name, app_sessions in self._sessions.items():
            for user_id, user_sessions in app_sessions.items():
                for session_id, session_with_ttl in user_sessions.items():
                    if session_with_ttl.ttl.is_expired(now):
                        if app_name not in expired_sessions:
                            expired_sessions[app_name] = {}
                        if user_id not in expired_sessions[app_name]:
                            expired_sessions[app_name][user_id] = []
                        expired_sessions[app_name][user_id].append(session_id)
                        logger.debug("Marked expired session: %s/%s/%s", app_name, user_id, session_id)

        # Collect expired user states
        for app_name, app_user_states in self._user_state.items():
            for user_id, user_state in app_user_states.items():
                if user_state.ttl.is_expired(now):
                    if app_name not in expired_user_states:
                        expired_user_states[app_name] = []
                    expired_user_states[app_name].append(user_id)
                    logger.debug("Marked expired user state: %s/%s", app_name, user_id)

        # Collect expired app states
        for app_name, app_state in self._app_state.items():
            if app_state.ttl.is_expired(now):
                expired_app_states.append(app_name)
                logger.debug("Marked expired app state: %s", app_name)

        # Phase 2: Delete collected expired items
        total_deleted = 0

        # Delete expired sessions
        for app_name, app_sessions in expired_sessions.items():
            for user_id, session_ids in app_sessions.items():
                for session_id in session_ids:
                    del self._sessions[app_name][user_id][session_id]
                    total_deleted += 1
                # Clean up empty user session dict
                if not self._sessions[app_name][user_id]:
                    del self._sessions[app_name][user_id]
            # Clean up empty app session dict
            if not self._sessions[app_name]:
                del self._sessions[app_name]

        # Delete expired user states
        for app_name, user_ids in expired_user_states.items():
            for user_id in user_ids:
                del self._user_state[app_name][user_id]
                total_deleted += 1
            # Clean up empty app user state dict
            if not self._user_state[app_name]:
                del self._user_state[app_name]

        # Delete expired app states
        for app_name in expired_app_states:
            del self._app_state[app_name]
            total_deleted += 1

        if total_deleted > 0:
            session_count = sum(
                len(sessions) for app_sessions in expired_sessions.values() for sessions in app_sessions.values())
            user_state_count = sum(len(users) for users in expired_user_states.values())
            app_state_count = len(expired_app_states)
            logger.info("Cleanup completed: deleted %s items (%s sessions, %s user states, %s app states)",
                        total_deleted, session_count, user_state_count, app_state_count)

    async def _cleanup_loop(self) -> None:
        """Background task for periodic cleanup of expired sessions and states."""
        logger.info("Cleanup task started with interval: %ss", self._session_config.ttl.cleanup_interval_seconds)

        try:
            while not self.__cleanup_stop_event.is_set():
                try:
                    # Wait for the cleanup interval or stop event
                    await asyncio.wait_for(self.__cleanup_stop_event.wait(),
                                           timeout=self._session_config.ttl.cleanup_interval_seconds)
                    # If we get here, stop event was set
                    break
                except asyncio.TimeoutError:
                    # Timeout means it's time to cleanup
                    try:
                        self._cleanup_expired()
                        logger.debug("Cleanup cycle completed")
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.error("Error during cleanup: %s", ex, exc_info=True)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Cleanup loop encountered error: %s", ex, exc_info=True)
        finally:
            logger.info("Cleanup task stopped")

    def _start_cleanup_task(self) -> None:
        """Start the background cleanup task."""
        if not self._session_config.need_ttl_expire():
            logger.debug("Cleanup task disabled (ttl is disabled)")
            return

        if self.__cleanup_task is not None:
            logger.debug("Cleanup task is already running")
            return

        self.__cleanup_stop_event = asyncio.Event()
        self.__cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("Cleanup task created")

    def _stop_cleanup_task(self) -> None:
        """Stop the background cleanup task."""
        if self.__cleanup_task is None:
            return

        if self.__cleanup_stop_event is not None:
            self.__cleanup_stop_event.set()

        if self.__cleanup_task and not self.__cleanup_task.done():
            self.__cleanup_task.cancel()

        self.__cleanup_task = None
        self.__cleanup_stop_event = None
        logger.debug("Cleanup task stopped")

    @override
    async def close(self) -> None:
        """Close the service and stop cleanup task."""
        self._stop_cleanup_task()
        await super().close()

    def _update_app_state(self, app_name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update app state with TTL.

        Args:
            app_name: Application name
            data: Data to update
        Returns:
            Updated app state
        """
        if app_name not in self._app_state:
            self._app_state[app_name] = StateWithTTL(data=data, ttl=self._session_config.ttl)
            return data
        return self._app_state[app_name].update(data)

    def _update_user_state(self, app_name: str, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update user state with TTL.

        Args:
            app_name: Application name
            user_id: User ID
            data: Data to update
        Returns:
            Updated user state
        """
        if app_name not in self._user_state:
            self._user_state[app_name] = {}
        if user_id not in self._user_state[app_name]:
            self._user_state[app_name][user_id] = StateWithTTL(data=data, ttl=self._session_config.ttl)
            return data
        return self._user_state[app_name][user_id].update(data)

    def _set_session(self, app_name: str, user_id: str, session_id: str, session: Session) -> None:
        """Set a session to the in-memory storage.

        Args:
            app_name: Application name
            user_id: User ID
            session_id: Session ID
            session: Session to set
        """
        # Initialize storage structures
        if app_name not in self._sessions:
            self._sessions[app_name] = {}
        if user_id not in self._sessions[app_name]:
            self._sessions[app_name][user_id] = {}

        # Store session with TTL
        session_with_ttl = SessionWithTTL(session=session, ttl=self._session_config.ttl)
        session_with_ttl.update(session)
        self._sessions[app_name][user_id][session_id] = session_with_ttl

    def _get_app_state(self, app_name: str) -> dict[str, Any]:
        """Get app state with TTL.

        Args:
            app_name: Application name
        Returns:
            App state
        """
        if app_name not in self._app_state:
            return {}
        return self._app_state[app_name].get()

    def _get_user_state(self, app_name: str, user_id: str) -> dict[str, Any]:
        """Get user state with TTL.

        Args:
            app_name: Application name
            user_id: User ID
        Returns:
            User state
        """
        if app_name not in self._user_state:
            return {}
        if user_id not in self._user_state[app_name]:
            return {}
        return self._user_state[app_name][user_id].get()

    def _get_session(self, app_name: str, user_id: str, session_id: str) -> Optional[Session]:
        """Get a session from the in-memory storage.

        Args:
            app_name: Application name
            user_id: User ID
            session_id: Session ID
        Returns:
            Session
        """
        if app_name not in self._sessions:
            return None
        if user_id not in self._sessions[app_name]:
            return None
        if session_id not in self._sessions[app_name][user_id]:
            return None
        return self._sessions[app_name][user_id][session_id].get()

    def _merge_state(self, app_state: dict[str, Any], user_state: dict[str, Any], copied_session: Session) -> Session:
        """Merge app, user, and session state into the session object.

        Args:
            app_state: Application state
            user_state: User state
            copied_session: Session to merge state into

        Returns:
            Session with merged state
        """
        # Merge states
        merge_state(StateStorageEntry(app_state_delta=app_state,
                                      user_state_delta=user_state,
                                      session_state=copied_session.state),
                    need_copy=False)
        return copied_session

    def _is_session_exist(self, app_name: str, user_id: str, session_id: str) -> bool:
        """Check if a session exists and is not expired.

        Args:
            app_name: Application name
            user_id: User ID
            session_id: Session ID

        Returns:
            True if session exists, False if session is expired or not found
        """
        session = self._get_session(app_name, user_id, session_id)
        return session is not None
