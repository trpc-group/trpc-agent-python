# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""UTF-8 JSONL audit sink for redacted safety observations."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ._models import SafetyObservation
from ._monitor import MonitorSink


class JsonlAuditSink(MonitorSink):
    """Append one observation per line with an in-process lock.

    One instance is safe for threads in one process. Multiple processes must
    use distinct paths or an external writer service.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event: SafetyObservation) -> None:
        if not isinstance(event, SafetyObservation):
            return
        payload = event.model_dump(mode="json", exclude_none=True)
        line = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.write("\n")

    def read_events(self, *, ignore_partial_tail: bool = True) -> list[dict[str, Any]]:
        """Read complete JSONL records, optionally ignoring one partial tail."""
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                if ignore_partial_tail and index == len(lines) - 1:
                    break
                raise
            if isinstance(value, dict):
                events.append(value)
        return events
