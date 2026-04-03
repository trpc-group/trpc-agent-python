# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run cancellation manager for TRPC Agent framework.

This module provides the core cancellation mechanism for agent runs, including
session-based tracking and cooperative cancellation support.
"""

import asyncio
from dataclasses import dataclass
from typing import Dict
from typing import Optional

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.utils import SingletonBase


@dataclass(frozen=True)
class SessionKey:
    """Immutable key for identifying a session."""
    app_name: str
    user_id: str
    session_id: str


class _RunCancellationManager(SingletonBase):
    """Manages cancellation state for agent runs.

    This manager is async-safe and uses asyncio primitives for synchronization.
    It tracks active runs and their cancellation status using SessionKey directly.
    """

    def __init__(self):
        super().__init__()
        # Maps session key to cancellation event
        self._cancelled: Dict[SessionKey, asyncio.Event] = {}
        # Maps session key to cleanup completion event (for cancel_run_async to wait on)
        self._cleanup_events: Dict[SessionKey, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def register_run(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> SessionKey:
        """Register a new run for cancellation tracking.

        Args:
            app_name: The application name.
            user_id: The user ID.
            session_id: The session ID.

        Returns:
            SessionKey for use in cancellation checks.
        """
        session_key = SessionKey(app_name, user_id, session_id)
        async with self._lock:
            # Create cancellation event for this run
            self._cancelled[session_key] = asyncio.Event()
            logger.debug("Registered run for session (%s, %s, %s)", app_name, user_id, session_id)
        return session_key

    async def cancel_run(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> Optional[asyncio.Event]:
        """Request cancellation of a run by session info.

        This is the primary cancellation API for users.

        Args:
            app_name: The application name.
            user_id: The user ID.
            session_id: The session ID.

        Returns:
            An asyncio.Event that will be set when cleanup_run is called,
            or None if no active run found for this session.
        """
        session_key = SessionKey(app_name, user_id, session_id)
        async with self._lock:
            if session_key in self._cancelled:
                self._cancelled[session_key].set()
                # Create cleanup event for waiting
                cleanup_event = asyncio.Event()
                self._cleanup_events[session_key] = cleanup_event
                logger.info("Run marked for cancellation (app_name: %s)(user: %s)(session: %s)", app_name, user_id,
                            session_id)
                return cleanup_event
            else:
                logger.debug("No active run found for session (%s, %s, %s)", app_name, user_id, session_id)
                return None

    async def is_cancelled(self, session_key: SessionKey) -> bool:
        """Check if a run is cancelled.

        This is an async method that acquires the lock to safely check
        cancellation status.

        Args:
            session_key: The session key to check.

        Returns:
            True if cancelled, False otherwise.
        """
        async with self._lock:
            event = self._cancelled.get(session_key)
            cancel_flag = event.is_set() if event else False
            return cancel_flag

    async def cleanup_run(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        """Remove a run from tracking.

        Should be called when a run completes (normally or cancelled).

        Args:
            app_name: The application name.
            user_id: The user ID.
            session_id: The session ID.
        """
        session_key = SessionKey(app_name, user_id, session_id)
        async with self._lock:
            if session_key in self._cancelled:
                del self._cancelled[session_key]
            # Signal cleanup completion to any waiters
            if session_key in self._cleanup_events:
                self._cleanup_events[session_key].set()
                del self._cleanup_events[session_key]
            logger.debug("Cleaned up run from cancellation tracking (session: %s)", session_id)

    def get_active_sessions(self) -> Dict[tuple[str, str, str], bool]:
        """Get all active sessions and their cancellation status.

        Returns:
            Dict mapping (app_name, user_id, session_id) to is_cancelled status.
        """
        return {(k.app_name, k.user_id, k.session_id): v.is_set() for k, v in self._cancelled.items()}

    async def get_cancel_event(self, session_key: SessionKey) -> Optional[asyncio.Event]:
        """Get the cancellation event for a session.

        Args:
            session_key: The session key to get the event for.

        Returns:
            The asyncio.Event that will be set when cancellation is requested,
            or None if the session is not registered.
        """
        async with self._lock:
            event = self._cancelled.get(session_key)
            return event if event else None


# Module-level singleton
_manager = _RunCancellationManager()


async def register_run(
    app_name: str,
    user_id: str,
    session_id: str,
) -> SessionKey:
    """Register a run for cancellation tracking."""
    return await _manager.register_run(app_name, user_id, session_id)


async def cancel_run(
    app_name: str,
    user_id: str,
    session_id: str,
) -> Optional[asyncio.Event]:
    """Request cancellation of a run by session info.

    Returns:
        An asyncio.Event that will be set when cleanup_run is called,
        or None if no active run found for this session.
    """
    return await _manager.cancel_run(app_name, user_id, session_id)


async def is_run_cancelled(session_key: SessionKey) -> bool:
    """Check if a run is cancelled."""
    return await _manager.is_cancelled(session_key)


async def cleanup_run(
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """Remove a run from tracking."""
    await _manager.cleanup_run(app_name, user_id, session_id)


async def raise_if_cancelled(session_key: SessionKey) -> None:
    """Check and raise RunCancelledException if cancelled.

    This is the primary checkpoint function to be called throughout
    agent execution.

    Args:
        session_key: The session key to check.

    Raises:
        RunCancelledException: If the run is cancelled.
    """
    if await _manager.is_cancelled(session_key):
        from trpc_agent_sdk.exceptions import RunCancelledException
        logger.info("Cancelling run for session %s", session_key.session_id)
        raise RunCancelledException(f"Run for session {session_key.session_id} was cancelled")


async def get_cancel_event(session_key: SessionKey) -> Optional[asyncio.Event]:
    """Get the cancellation event for a session."""
    return await _manager.get_cancel_event(session_key)
