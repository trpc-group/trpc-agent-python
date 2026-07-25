# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Audit event sinks for tool safety decisions."""

from __future__ import annotations

import json
from abc import ABC
from abc import abstractmethod
from pathlib import Path
from threading import Lock
from typing import Optional

from ._models import SafetyAuditEvent


class SafetyAuditSink(ABC):
    """Consumer interface for compact safety monitoring events."""

    @abstractmethod
    def emit(self, event: SafetyAuditEvent) -> None:
        """Persist or forward one audit event."""


class NullAuditSink(SafetyAuditSink):
    """Discard events when audit persistence is intentionally disabled."""

    def emit(self, event: SafetyAuditEvent) -> None:
        return None


class MemoryAuditSink(SafetyAuditSink):
    """In-memory event sink useful for tests and custom adapters."""

    def __init__(self):
        self.events: list[SafetyAuditEvent] = []

    def emit(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)


class JsonlAuditSink(SafetyAuditSink):
    """Append script-free, JSON-encoded audit events to a local file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = Lock()

    def emit(self, event: SafetyAuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json(exclude_none=True)
        with self._lock, self.path.open("a", encoding="utf-8") as file:
            file.write(line)
            file.write("\n")


class CallableAuditSink(SafetyAuditSink):
    """Adapter for monitoring clients that expose a plain callback."""

    def __init__(self, callback):
        self.callback = callback

    def emit(self, event: SafetyAuditEvent) -> None:
        self.callback(json.loads(event.model_dump_json()))


def ensure_audit_sink(sink: Optional[SafetyAuditSink]) -> SafetyAuditSink:
    """Use an explicit no-op sink instead of branching at each call site."""
    return sink or NullAuditSink()
