# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""Session manager that adds production features to TRPC's native session service."""

import asyncio
import time
from typing import Any
from typing import Dict
from typing import Optional
from typing import Set
from typing import Union

from trpc_agent_sdk.log import logger


class SessionManager:
    """Session manager that wraps TRPC's session service.

    Adds essential production features:
    - Timeout monitoring based on TRPC's lastUpdateTime
    - Cross-user/app session enumeration
    - Per-user session limits
    - Automatic cleanup of expired sessions
    - Optional automatic session memory on deletion
    - State management and updates
    """

    _instance = None
    _initialized = False

    def __new__(cls, session_service=None, **kwargs):
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        session_service=None,
        memory_service=None,
        session_timeout_seconds: int = 1200,  # 20 minutes default
        cleanup_interval_seconds: int = 300,  # 5 minutes
        max_sessions_per_user: Optional[int] = None,
        auto_cleanup: bool = True,
    ):
        """Initialize the session manager.

        Args:
            session_service: TRPC session service (required on first initialization)
            memory_service: Optional TRPC memory service for automatic session memory
            session_timeout_seconds: Time before a session is considered expired
            cleanup_interval_seconds: Interval between cleanup cycles
            max_sessions_per_user: Maximum concurrent sessions per user (None = unlimited)
            auto_cleanup: Enable automatic session cleanup task
        """
        if self._initialized:
            return

        if session_service is None:
            from trpc_agent_sdk.sessions import InMemorySessionService

            session_service = InMemorySessionService()

        self._session_service = session_service
        self._memory_service = memory_service
        self._timeout = session_timeout_seconds
        self._cleanup_interval = cleanup_interval_seconds
        self._max_per_user = max_sessions_per_user
        self._auto_cleanup = auto_cleanup

        # Minimal tracking: just keys and user counts
        self._session_keys: Set[str] = set()  # "app_name:session_id" keys
        self._user_sessions: Dict[str, Set[str]] = {}  # user_id -> set of session_keys

        self._cleanup_task: Optional[asyncio.Task] = None
        self._initialized = True

        logger.info("Initialized SessionManager - timeout: %ss, cleanup: %ss, max/user: %s, memory: %s",
                    session_timeout_seconds, cleanup_interval_seconds, max_sessions_per_user or 'unlimited',
                    'enabled' if memory_service else 'disabled')

    @classmethod
    def get_instance(cls, **kwargs):
        """Get the singleton instance."""
        return cls(**kwargs)

    @classmethod
    def reset_instance(cls):
        """Reset singleton for testing."""
        if cls._instance and hasattr(cls._instance, "_cleanup_task"):
            task = cls._instance._cleanup_task
            if task:
                try:
                    task.cancel()
                except RuntimeError:
                    pass
        cls._instance = None
        cls._initialized = False

    async def get_or_create_session(self,
                                    session_id: str,
                                    app_name: str,
                                    user_id: str,
                                    initial_state: Optional[Dict[str, Any]] = None) -> Any:
        """Get existing session or create new one.

        Returns the TRPC session object directly.
        """
        session_key = f"{app_name}:{session_id}"

        # Check user limits before creating
        if session_key not in self._session_keys and self._max_per_user:
            user_count = len(self._user_sessions.get(user_id, set()))
            if user_count >= self._max_per_user:
                # Remove oldest session for this user
                await self._remove_oldest_user_session(user_id)

        # Get or create via TRPC
        session = await self._session_service.get_session(session_id=session_id, app_name=app_name, user_id=user_id)

        if not session:
            session = await self._session_service.create_session(session_id=session_id,
                                                                 user_id=user_id,
                                                                 app_name=app_name,
                                                                 state=initial_state or {})
            logger.info("Created new session: %s", session_key)
        else:
            logger.debug("Retrieved existing session: %s", session_key)

        # Track the session key
        self._track_session(session_key, user_id)

        # Start cleanup if needed
        if self._auto_cleanup and not self._cleanup_task:
            self._start_cleanup_task()

        return session

    # ===== STATE MANAGEMENT METHODS =====

    async def update_session_state(self,
                                   session_id: str,
                                   app_name: str,
                                   user_id: str,
                                   state_updates: Dict[str, Any],
                                   merge: bool = True) -> bool:
        """Update session state with new values.

        Args:
            session_id: Session identifier
            app_name: Application name
            user_id: User identifier
            state_updates: Dictionary of state key-value pairs to update
            merge: If True, merge with existing state; if False, replace completely

        Returns:
            True if successful, False otherwise
        """
        try:
            session = await self._session_service.get_session(session_id=session_id, app_name=app_name, user_id=user_id)

            if not session:
                logger.debug(
                    "Session not found for update: %s:%s - this may be normal if session is still being created",
                    app_name, session_id)
                return False

            if not state_updates:
                logger.debug("No state updates provided for session: %s:%s", app_name, session_id)
                return False

            # Apply state updates using EventActions
            from trpc_agent_sdk.events import Event
            from trpc_agent_sdk.types import EventActions

            # Prepare state delta
            if merge:
                # Merge with existing state
                state_delta = state_updates
            else:
                # Replace entire state
                state_delta = state_updates
                # Note: Complete replacement might need clearing existing keys
                # This depends on TRPC's behavior - may need to explicitly clear

            # Create event with state changes
            actions = EventActions(state_delta=state_delta)
            event = Event(
                invocation_id=f"state_update_{int(time.time())}",
                author="system",
                actions=actions,
                timestamp=time.time(),
                partial=True,
            )

            # Apply changes through TRPC's event system
            await self._session_service.append_event(session, event)

            logger.debug("Updated state for session %s:%s", app_name, session_id)
            logger.debug("State updates: %s", state_updates)

            return True

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to update session state: %s", ex, exc_info=True)
            return False

    async def get_session_state(self, session_id: str, app_name: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get current session state.

        Args:
            session_id: Session identifier
            app_name: Application name
            user_id: User identifier

        Returns:
            Session state dictionary or None if session not found
        """
        try:
            session = await self._session_service.get_session(session_id=session_id, app_name=app_name, user_id=user_id)

            if not session:
                logger.debug("Session not found when getting state: %s:%s", app_name, session_id)
                return None

            # Return state as dictionary
            if hasattr(session.state, "to_dict"):
                return session.state.to_dict()
            else:
                # Fallback for dict-like state objects
                return dict(session.state)

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to get session state: %s", ex, exc_info=True)
            return None

    async def get_state_value(self, session_id: str, app_name: str, user_id: str, key: str, default: Any = None) -> Any:
        """Get a specific value from session state.

        Args:
            session_id: Session identifier
            app_name: Application name
            user_id: User identifier
            key: State key to retrieve
            default: Default value if key not found

        Returns:
            Value for the key or default
        """
        try:
            session = await self._session_service.get_session(session_id=session_id, app_name=app_name, user_id=user_id)

            if not session:
                logger.debug("Session not found when getting state value: %s:%s", app_name, session_id)
                return default

            if hasattr(session.state, "get"):
                return session.state.get(key, default)
            else:
                return session.state.get(key, default) if key in session.state else default

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to get state value: %s", ex, exc_info=True)
            return default

    async def set_state_value(self, session_id: str, app_name: str, user_id: str, key: str, value: Any) -> bool:
        """Set a specific value in session state.

        Args:
            session_id: Session identifier
            app_name: Application name
            user_id: User identifier
            key: State key to set
            value: Value to set

        Returns:
            True if successful, False otherwise
        """
        return await self.update_session_state(session_id=session_id,
                                               app_name=app_name,
                                               user_id=user_id,
                                               state_updates={key: value})

    async def remove_state_keys(self, session_id: str, app_name: str, user_id: str, keys: Union[str, list]) -> bool:
        """Remove specific keys from session state.

        Args:
            session_id: Session identifier
            app_name: Application name
            user_id: User identifier
            keys: Single key or list of keys to remove

        Returns:
            True if successful, False otherwise
        """
        try:
            if isinstance(keys, str):
                keys = [keys]

            # Get current state
            current_state = await self.get_session_state(session_id, app_name, user_id)
            if not current_state:
                return False

            # Create state delta to remove keys (set to None for removal)
            state_delta = {key: None for key in keys if key in current_state}

            if not state_delta:
                logger.debug("No keys to remove from session %s:%s", app_name, session_id)
                return True

            return await self.update_session_state(session_id=session_id,
                                                   app_name=app_name,
                                                   user_id=user_id,
                                                   state_updates=state_delta)

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to remove state keys: %s", ex, exc_info=True)
            return False

    async def clear_session_state(self,
                                  session_id: str,
                                  app_name: str,
                                  user_id: str,
                                  preserve_prefixes: Optional[list] = None) -> bool:
        """Clear session state, optionally preserving certain prefixes.

        Args:
            session_id: Session identifier
            app_name: Application name
            user_id: User identifier
            preserve_prefixes: List of prefixes to preserve (e.g., ['user:', 'app:'])

        Returns:
            True if successful, False otherwise
        """
        try:
            current_state = await self.get_session_state(session_id, app_name, user_id)
            if not current_state:
                return False

            preserve_prefixes = preserve_prefixes or []

            # Determine which keys to remove
            keys_to_remove = []
            for key in current_state.keys():
                should_preserve = any(key.startswith(prefix) for prefix in preserve_prefixes)
                if not should_preserve:
                    keys_to_remove.append(key)

            if keys_to_remove:
                return await self.remove_state_keys(session_id=session_id,
                                                    app_name=app_name,
                                                    user_id=user_id,
                                                    keys=keys_to_remove)

            return True

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to clear session state: %s", ex, exc_info=True)
            return False

    async def initialize_session_state(
        self,
        session_id: str,
        app_name: str,
        user_id: str,
        initial_state: Dict[str, Any],
        overwrite_existing: bool = False,
    ) -> bool:
        """Initialize session state with default values.

        Args:
            session_id: Session identifier
            app_name: Application name
            user_id: User identifier
            initial_state: Initial state values
            overwrite_existing: Whether to overwrite existing values

        Returns:
            True if successful, False otherwise
        """
        try:
            if not overwrite_existing:
                # Only set values that don't already exist
                current_state = await self.get_session_state(session_id, app_name, user_id)
                if current_state:
                    # Filter out keys that already exist
                    filtered_state = {key: value for key, value in initial_state.items() if key not in current_state}
                    if not filtered_state:
                        logger.debug("No new state values to initialize for session %s:%s", app_name, session_id)
                        return True
                    initial_state = filtered_state

            return await self.update_session_state(session_id=session_id,
                                                   app_name=app_name,
                                                   user_id=user_id,
                                                   state_updates=initial_state)

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to initialize session state: %s", ex, exc_info=True)
            return False

    # ===== BULK STATE OPERATIONS =====

    async def bulk_update_user_state(self,
                                     user_id: str,
                                     state_updates: Dict[str, Any],
                                     app_name_filter: Optional[str] = None) -> Dict[str, bool]:
        """Update state across all sessions for a user.

        Args:
            user_id: User identifier
            state_updates: State updates to apply
            app_name_filter: Optional filter for specific app

        Returns:
            Dictionary mapping session_key to success status
        """
        results = {}

        if user_id not in self._user_sessions:
            logger.debug("No sessions found for user %s", user_id)
            return results

        for session_key in self._user_sessions[user_id]:
            app_name, session_id = session_key.split(":", 1)

            # Apply filter if specified
            if app_name_filter and app_name != app_name_filter:
                continue

            success = await self.update_session_state(session_id=session_id,
                                                      app_name=app_name,
                                                      user_id=user_id,
                                                      state_updates=state_updates)

            results[session_key] = success

        return results

    # ===== EXISTING METHODS (unchanged) =====

    def _track_session(self, session_key: str, user_id: str):
        """Track a session key for enumeration."""
        self._session_keys.add(session_key)

        if user_id not in self._user_sessions:
            self._user_sessions[user_id] = set()
        self._user_sessions[user_id].add(session_key)

    def _untrack_session(self, session_key: str, user_id: str):
        """Remove session tracking."""
        self._session_keys.discard(session_key)

        if user_id in self._user_sessions:
            self._user_sessions[user_id].discard(session_key)
            if not self._user_sessions[user_id]:
                del self._user_sessions[user_id]

    async def _remove_oldest_user_session(self, user_id: str):
        """Remove the oldest session for a user based on lastUpdateTime."""
        if user_id not in self._user_sessions:
            return

        oldest_session = None
        oldest_time = float("inf")

        # Find oldest session by checking TRPC's lastUpdateTime
        for session_key in self._user_sessions[user_id]:
            app_name, session_id = session_key.split(":", 1)
            try:
                session = await self._session_service.get_session(session_id=session_id,
                                                                  app_name=app_name,
                                                                  user_id=user_id)
                if session and hasattr(session, "last_update_time"):
                    update_time = session.last_update_time
                    if update_time < oldest_time:
                        oldest_time = update_time
                        oldest_session = session
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error checking session %s: %s", session_key, ex)

        if oldest_session:
            session_key = f"{oldest_session.app_name}:{oldest_session.id}"
            await self._delete_session(oldest_session)
            logger.info("Removed oldest session for user %s: %s", user_id, session_key)

    async def _delete_session(self, session):
        """Delete a session using the session object directly.

        Args:
            session: The TRPC session object to delete
        """
        if not session:
            logger.warning("Cannot delete None session")
            return

        session_key = f"{session.app_name}:{session.id}"

        try:
            await self._session_service.delete_session(session_id=session.id,
                                                       app_name=session.app_name,
                                                       user_id=session.user_id)
            logger.debug("Deleted session: %s", session_key)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to delete session %s: %s", session_key, ex)

        self._untrack_session(session_key, session.user_id)

    def _start_cleanup_task(self):
        """Start the cleanup task if not already running."""
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._cleanup_loop())
            logger.debug("Started session cleanup task %s for SessionManager %s", id(self._cleanup_task), id(self))
        except RuntimeError:
            logger.debug("No event loop, cleanup will start later")

    async def _cleanup_loop(self):
        """Periodically clean up expired sessions."""
        logger.debug("Cleanup loop started for SessionManager %s", id(self))
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                logger.debug("Running cleanup on SessionManager %s", id(self))
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                logger.info("Cleanup task cancelled")
                break
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Cleanup error: %s", ex, exc_info=True)

    async def _cleanup_expired_sessions(self):
        """Find and remove expired sessions based on lastUpdateTime."""
        current_time = time.time()
        expired_count = 0

        # Check all tracked sessions
        for session_key in list(self._session_keys):  # Copy to avoid modification during iteration
            app_name, session_id = session_key.split(":", 1)

            # Find user_id for this session
            user_id = None
            for uid, keys in self._user_sessions.items():
                if session_key in keys:
                    user_id = uid
                    break

            if not user_id:
                continue

            try:
                session = await self._session_service.get_session(session_id=session_id,
                                                                  app_name=app_name,
                                                                  user_id=user_id)

                if session and hasattr(session, "last_update_time"):
                    age = current_time - session.last_update_time
                    if age > self._timeout:
                        # Check for pending tool calls before deletion (HITL scenarios)
                        pending_calls = session.state.get("pending_tool_calls", []) if session.state else []
                        if pending_calls:
                            logger.info("Preserving expired session %s - has %s pending tool calls (HITL)", session_key,
                                        len(pending_calls))
                        else:
                            await self._delete_session(session)
                            expired_count += 1
                elif not session:
                    # Session doesn't exist, just untrack it
                    self._untrack_session(session_key, user_id)

            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error checking session %s: %s", session_key, ex)

        if expired_count > 0:
            logger.info("Cleaned up %s expired sessions", expired_count)

    def get_session_count(self) -> int:
        """Get total number of tracked sessions."""
        return len(self._session_keys)

    def get_user_session_count(self, user_id: str) -> int:
        """Get number of sessions for a user."""
        return len(self._user_sessions.get(user_id, set()))

    async def stop_cleanup_task(self):
        """Stop the cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
