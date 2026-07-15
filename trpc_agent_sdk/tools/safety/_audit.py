"""Audit sinks for persisted scan decisions.

All sinks receive a fully redacted :class:`SafetyAuditEvent`. The JSONL
sink performs short blocking appends inside ``asyncio.to_thread`` so the
calling Filter does not stall on disk I/O during request handling.

When ``audit.required`` is true the surrounding adapter treats an emit
failure as fail-closed: the safety filter blocks the request even when
the scanner returned ``allow``.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Protocol, runtime_checkable

from trpc_agent_sdk.tools.safety._exceptions import SafetyAuditError
from trpc_agent_sdk.tools.safety._models import SafetyAuditEvent


@runtime_checkable
class AuditSink(Protocol):
    """Async protocol. ``emit`` must not raise except for audit errors."""

    async def emit(self, event: SafetyAuditEvent) -> None: ...


class InMemoryAuditSink:
    """Test-only sink that keeps events in a list."""

    def __init__(self) -> None:
        self._events: list[SafetyAuditEvent] = []
        self._lock = threading.Lock()

    async def emit(self, event: SafetyAuditEvent) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._append, event)

    def _append(self, event: SafetyAuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[SafetyAuditEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class JsonlAuditSink:
    """Append-only JSON Lines sink.

    ``path`` is opened in append-binary mode for every event so concurrent
    processes do not clobber each other. The short critical section keeps
    lock contention low while still ordering writes from one process.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = os.fspath(path)
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()

    async def emit(self, event: SafetyAuditEvent) -> None:
        payload = json.dumps(
            event.model_dump(mode="json"), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False,
        ) + "\n"
        try:
            await asyncio.to_thread(self._write, payload)
        except OSError as exc:
            raise SafetyAuditError(
                f"failed to write audit event to {self._path}: {exc}"
            ) from exc

    def _write(self, payload: str) -> None:
        with self._thread_lock:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(payload)


class _NullAuditSink:
    """No-op sink for environments that disable audit entirely."""

    async def emit(self, event: SafetyAuditEvent) -> None:  # pragma: no cover
        return None


NullAuditSink = _NullAuditSink
