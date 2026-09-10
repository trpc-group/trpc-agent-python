# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Coordinate session memory operations across event loops."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator


class CrossLoopLock:
    """Async lock backed by a thread lock and safe across event loops."""

    _POLL_INTERVAL_SECONDS = 0.01

    def __init__(self) -> None:
        """Create an unlocked cross-loop lock."""
        self._lock = threading.Lock()

    async def acquire(self) -> bool:
        """Acquire the lock without blocking the current event loop."""
        while not await asyncio.to_thread(self._lock.acquire, False):
            await asyncio.sleep(self._POLL_INTERVAL_SECONDS)
        return True

    def release(self) -> None:
        """Release the underlying thread lock."""
        self._lock.release()

    async def __aenter__(self) -> "CrossLoopLock":
        """Acquire the lock for an async context."""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Release the lock after an async context."""
        self.release()


class SessionOperationCoordinator:
    """Use thread locks to coordinate the main loop and post-turn worker."""

    def __init__(self) -> None:
        """Initialize the session lock table and its guard lock."""
        self._locks: dict[str, CrossLoopLock] = {}
        self._locks_guard = threading.Lock()

    def _session_lock(self, session_id: str) -> CrossLoopLock:
        """Return a session thread lock usable across event loops."""
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = CrossLoopLock()
                self._locks[session_id] = lock
            return lock

    @asynccontextmanager
    async def guard(
        self,
        session_id: str,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[bool]:
        """Wait asynchronously for a session lock and report acquisition."""
        lock = self._session_lock(session_id)
        if timeout is None:
            acquired = await lock.acquire()
        else:
            try:
                acquired = await asyncio.wait_for(lock.acquire(), timeout=timeout)
            except asyncio.TimeoutError:
                acquired = False
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
