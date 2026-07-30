#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Redacted audit sinks for tool safety decisions."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Protocol

from trpc_agent_sdk.log import logger

from ._models import SafetyAuditEvent


class SafetyAuditSink(Protocol):
    """Synchronous sink called before an execution is allowed."""

    def emit(self, event: SafetyAuditEvent) -> None:
        """Persist one already-redacted event."""


class LoggingAuditSink:
    """Emit structured audit events through the SDK logger."""

    def emit(self, event: SafetyAuditEvent) -> None:
        logger.info("tool safety audit: %s", event.model_dump_json())


class JsonlAuditSink:
    """Append one JSON object per line."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, event: SafetyAuditEvent) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
                stream.flush()


class MemoryAuditSink:
    """Small deterministic sink for examples and tests."""

    def __init__(self):
        self.events: list[SafetyAuditEvent] = []

    def emit(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)
