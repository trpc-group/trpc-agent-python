# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Best-effort audit sinks for tool safety decisions."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable
from typing import Protocol
from typing import Union
from typing import runtime_checkable

from trpc_agent_sdk.log import logger

from ._models import SafetyAuditEvent

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl.
    fcntl = None  # type: ignore[assignment]


@runtime_checkable
class AuditSink(Protocol):
    """Receives one already-redacted audit event."""

    def record(self, event: SafetyAuditEvent) -> None:
        """Persist or forward ``event``."""


AuditCallback = Callable[[SafetyAuditEvent], None]
AuditTarget = Union[AuditSink, AuditCallback]

_path_locks_guard = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.expanduser().resolve(strict=False))
    with _path_locks_guard:
        return _path_locks.setdefault(key, threading.Lock())


class JsonlAuditSink:
    """Append redacted events to JSONL with process-safe append semantics.

    A per-path thread lock prevents interleaving between sink instances in the
    same process. ``O_APPEND`` plus a single ``os.write`` makes each line a
    best-effort atomic append when multiple processes share the file.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = _lock_for(self.path)

    def record(self, event: SafetyAuditEvent) -> None:
        """Append one compact JSON object followed by a newline."""

        payload = (event.model_dump_json() + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            locked = False
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    locked = True
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("short write while appending safety audit event")
                    remaining = remaining[written:]
            finally:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def __call__(self, event: SafetyAuditEvent) -> None:
        """Allow a JSONL sink to be supplied wherever a callback is accepted."""

        self.record(event)


def record_audit_event(target: AuditTarget | None, event: SafetyAuditEvent) -> None:
    """Record an event without allowing audit failures to change a decision."""

    if target is None:
        return
    try:
        recorder = getattr(target, "record", None)
        if callable(recorder):
            recorder(event)
        elif callable(target):
            target(event)
        else:
            raise TypeError(f"unsupported safety audit target: {type(target).__name__}")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Unable to record tool safety audit event: %s", exc)
