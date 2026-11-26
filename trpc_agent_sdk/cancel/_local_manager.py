# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""In-process cancel and steering manager."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from dataclasses import field
from typing import Deque
from typing import Dict
from typing import List
from typing import Optional

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content

from ._base import CancelManager
from ._types import SessionKey
from ._types import Status


@dataclass
class _LocalRun:
    """In-process state for one active run."""

    key: SessionKey
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_event: asyncio.Event = field(default_factory=asyncio.Event)
    steering: Deque[Content] = field(default_factory=deque)


class _LocalManager(CancelManager):
    """Process-memory registry of active runs and their steering queues."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[SessionKey, _LocalRun] = {}

    def _get_run(self, key: SessionKey) -> Optional[_LocalRun]:
        with self._lock:
            return self._runs.get(key)

    def _pop_run(self, key: SessionKey) -> Optional[_LocalRun]:
        with self._lock:
            return self._runs.pop(key, None)

    async def register_run(self, key: SessionKey) -> None:
        """Register one active run for ``key``."""
        with self._lock:
            if key in self._runs:
                raise RuntimeError(f"Session {key.session_id} already has an active run")
            self._runs[key] = _LocalRun(key)

    async def unregister_run(self, key: SessionKey) -> None:
        """Release one run. Safe to call more than once."""
        run = self._pop_run(key)
        if run is None:
            return
        run.steering.clear()
        run.cleanup_event.set()

    async def cancel_run(self, key: SessionKey, timeout: float = 1.0) -> Status:
        """Request cancellation and wait for the run to finish cleanup.

        ``timeout`` is supplied by ``Runner.cancel_run_async``.
        """
        run = self._get_run(key)
        if run is None:
            return Status(ok=False, error_msg="no active run")

        run.cancel_event.set()
        try:
            await asyncio.wait_for(run.cleanup_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return Status(
                ok=False,
                error_msg=(f"Cancel timed out after {timeout}s: "
                           f"run still active for session {key.session_id}"),
            )
        logger.info("Cancel completed for session %s", key.session_id)
        return Status(ok=True)

    async def send_steering(self, key: SessionKey, content: Content) -> Status:
        """Enqueue one steering message for ``key``."""
        if content is None or not content.parts:
            return Status(ok=False, error_msg="steering content is empty")

        run = self._get_run(key)
        if run is None:
            return Status(ok=False, error_msg="no active run")
        with self._lock:
            run.steering.append(content)
        return Status(ok=True)

    async def drain_steering(self, key: SessionKey) -> List[Content]:
        """Take all pending steering messages for ``key``."""
        run = self._get_run(key)
        if run is None:
            return []
        with self._lock:
            contents = list(run.steering)
            run.steering.clear()
            return contents

    async def check_cancel(self, key: SessionKey) -> bool:
        """Return whether ``key`` has been cancelled."""
        run = self._get_run(key)
        return run is not None and run.cancel_event.is_set()

    async def get_cancel_event(self, key: SessionKey) -> Optional[asyncio.Event]:
        """Return the cancellation event for ``key``."""
        run = self._get_run(key)
        if run is None:
            return None
        return run.cancel_event


__all__ = [
    "_LocalManager",
]
