# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared-memory cancel and steering manager."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional

from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content

from ._shared_mem import _SharedMem
from ._shared_mem import _Slot
from ._types import SessionKey
from ._types import SteeringStatus

_CANCEL_WATCH_INTERVAL = 0.05


@dataclass
class _SharedRun:
    """Owner-side state for one active shared-memory run."""

    key: SessionKey
    slot: _Slot
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    watch_task: Optional[asyncio.Task] = None


class _SharedManager:
    """Run control backed by one shared-memory segment."""

    def __init__(self, mem: _SharedMem) -> None:
        self._mem = mem
        self._lock = threading.Lock()
        self._runs: Dict[SessionKey, _SharedRun] = {}
        self._closed = False

    def _get_run(self, key: SessionKey) -> Optional[_SharedRun]:
        with self._lock:
            return self._runs.get(key)

    def _pop_run(self, key: SessionKey) -> Optional[_SharedRun]:
        with self._lock:
            return self._runs.pop(key, None)

    def register_run(self, key: SessionKey) -> None:
        """Register one active run for ``key``."""
        slot = self._mem.allocate(key)
        try:
            with self._lock:
                if key in self._runs:
                    raise RuntimeError(f"Session {key.session_id} already has an active run")
                self._runs[key] = _SharedRun(key, slot)
        except Exception:
            self._mem.release(slot)
            raise

    def unregister_run(self, key: SessionKey) -> None:
        """Release one run. Safe to call more than once."""
        run = self._pop_run(key)
        if run is None:
            return
        self._cancel_watch_task(run)
        self._mem.release(run.slot)

    async def cancel_run(self, key: SessionKey, timeout: float = 1.0) -> bool:
        """Publish a cancel request. Shared mode does not wait for cleanup.

        ``timeout`` is accepted for interface compatibility and ignored.
        """
        run = self._get_run(key)
        if run is not None:
            run.cancel_event.set()
            self._mem.publish_cancel(run.slot)
            return True

        slot = self._mem.find(key)
        if slot is None:
            return False
        return self._mem.publish_cancel(slot)

    def send_steering(self, key: SessionKey, content: Content) -> SteeringStatus:
        """Enqueue one steering message for ``key``."""
        if content is None or not content.parts:
            return SteeringStatus.UNSUPPORTED_CONTENT

        run = self._get_run(key)
        if run is not None:
            return self._mem.enqueue(run.slot, content)

        slot = self._mem.find(key)
        if slot is None:
            return SteeringStatus.NO_ACTIVE_RUN
        return self._mem.enqueue(slot, content)

    def drain_steering(self, key: SessionKey) -> List[Content]:
        """Take all pending steering messages for ``key``."""
        run = self._get_run(key)
        if run is None:
            return []
        return self._mem.drain(run.slot)

    def raise_if_cancelled(self, key: SessionKey) -> None:
        """Raise ``RunCancelledException`` when ``key`` has been cancelled."""
        run = self._get_run(key)
        if run is None:
            return
        if not run.cancel_event.is_set() and not self._mem.is_cancelled(run.slot):
            return

        run.cancel_event.set()
        logger.info("Cancelling run for session %s", key.session_id)
        raise RunCancelledException(f"Run for session {key.session_id} was cancelled")

    def get_cancel_event(self, key: SessionKey) -> Optional[asyncio.Event]:
        """Return the cancellation event for ``key``.

        The first call for a run starts a per-run watcher task. The watcher
        polls the shared cancel flag and sets the local event, so a cancel
        published by another worker wakes local waiters.
        """
        run = self._get_run(key)
        if run is None:
            return None
        if run.cancel_event.is_set() or self._closed:
            return run.cancel_event
        if self._mem.is_cancelled(run.slot):
            run.cancel_event.set()
            return run.cancel_event

        if run.watch_task is None or run.watch_task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return run.cancel_event
            run.watch_task = loop.create_task(
                self._watch_run_cancel(run),
                name=f"cancel-watch:{key.session_id}",
            )
        return run.cancel_event

    async def _watch_run_cancel(self, run: _SharedRun) -> None:
        """Set ``run.cancel_event`` when the shared cancel flag is observed."""
        current = asyncio.current_task()
        try:
            while not run.cancel_event.is_set():
                if self._closed:
                    return
                if self._mem.is_cancelled(run.slot):
                    run.cancel_event.set()
                    logger.debug("Cancel signal observed for session %s", run.key.session_id)
                    return
                await asyncio.sleep(_CANCEL_WATCH_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:  # pylint: disable=broad-except
            logger.exception("Cancel watcher failed for session %s", run.key.session_id)
        finally:
            if run.watch_task is current:
                run.watch_task = None

    @staticmethod
    def _cancel_watch_task(run: _SharedRun) -> None:
        """Cancel a run's watcher task, if any."""
        task = run.watch_task
        run.watch_task = None
        if task is None or task.done():
            return

        loop = task.get_loop()
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        try:
            if current_loop is loop:
                task.cancel()
            elif loop.is_running():
                loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()
        except RuntimeError:
            pass

    def close(self) -> None:
        """Drop all owner-side state without destroying the shared memory."""
        if self._closed:
            return

        self._closed = True
        with self._lock:
            runs = list(self._runs.values())
            self._runs.clear()
        for run in runs:
            self._cancel_watch_task(run)
            self._mem.release(run.slot)


__all__ = [
    "_SharedManager",
]
