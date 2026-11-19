# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Base contract for cancel and steering backends."""

from __future__ import annotations

import asyncio
from abc import ABC
from abc import abstractmethod
from typing import List
from typing import Optional

from trpc_agent_sdk.types import Content

from ._types import SessionKey
from ._types import Status


class CancelManager(ABC):
    """Backend contract for cancel and steering control.

    One manager instance is active per process. A custom manager is installed
    once via ``set_cancel_manager()`` before any run starts and before forking
    workers. The instance is inherited by forked workers, so implementations
    should create process-local resources lazily.
    """

    @abstractmethod
    async def register_run(self, key: SessionKey) -> None:
        """Register one active run.

        Called by ``Runner`` before the agent starts. Raise ``RuntimeError``
        when the same key already has an active run.
        """

    @abstractmethod
    async def unregister_run(self, key: SessionKey) -> None:
        """Release one active run.

        Must be idempotent. The implementation should discard pending steering
        and wake callers waiting for cleanup, if it tracks cleanup.
        """

    @abstractmethod
    async def cancel_run(self, key: SessionKey, timeout: float = 1.0) -> Status:
        """Request cancellation for ``key``.

        ``timeout`` is supplied by ``Runner.cancel_run_async``. Implementations
        should wait up to this many seconds for cleanup and return
        ``Status(ok=False, error_msg=...)`` if the run is still active when it
        expires, so callers can distinguish "stopped" from "still running".

        Return ``Status(ok=False, error_msg=...)`` when no active run matches
        ``key``. A successful request returns ``Status(ok=True)`` only after
        the owner has finished cleanup.
        """

    @abstractmethod
    async def send_steering(self, key: SessionKey, content: Content) -> Status:
        """Enqueue one steering message for ``key``.

        Return ``Status(ok=True)`` when the message was enqueued. Return
        ``Status(ok=False, error_msg=...)`` for empty content, a missing active
        run, or a backend failure.
        """

    @abstractmethod
    async def drain_steering(self, key: SessionKey) -> List[Content]:
        """Take all pending steering messages for ``key``.

        The queue is drained in FIFO order. Return an empty list when no run
        matches or nothing is pending.
        """

    @abstractmethod
    async def check_cancel(self, key: SessionKey) -> bool:
        """Return whether ``key`` has been cancelled.

        Return ``False`` when no active run matches ``key``. This method only
        reports state; raising ``RunCancelledException`` is the caller's job.
        """

    @abstractmethod
    async def get_cancel_event(self, key: SessionKey) -> Optional[asyncio.Event]:
        """Return the cancellation event for a run owned by this process.

        Return ``None`` when no active run matches ``key``. The returned event
        must be set once cancellation is observed. Repeated calls for the same
        run must return an event with the same semantics.
        """


__all__ = [
    "CancelManager",
]
