# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logging for the Tool Script Safety Guard.

Writes structured JSON-lines audit events so that external monitoring
systems (ELK, Loki, Datadog …) can consume them.

The logger is intentionally simple: it appends one JSON object per line
to a file.  No external dependencies, no background threads — just
deterministic, append-only logging.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any
from typing import Optional
from typing import Union

from ._models import AuditEvent


class AuditLogger:
    """Append-only JSONL audit logger.

    Thread-safe via a single re-entrant lock.  If no file path is given,
    events are buffered in memory and can be retrieved via
    :meth:`get_events`.

    Args:
        path: Path to the JSONL audit file.  ``None`` to keep events in memory.
        max_buffer: Maximum number of in-memory events when no path is set.
    """

    def __init__(
        self,
        path: Optional[Union[str, os.PathLike]] = None,
        max_buffer: int = 10_000,
    ) -> None:
        self._path = str(path) if path else None
        self._max_buffer = max_buffer
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------

    def log(self, event: AuditEvent) -> None:
        """Write *event* to the audit log (file and/or buffer)."""
        data = event.to_dict()
        with self._lock:
            if self._path:
                # Ensure parent directory exists
                parent = os.path.dirname(self._path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            else:
                if len(self._buffer) >= self._max_buffer:
                    self._buffer.pop(0)
                self._buffer.append(data)

    # ------------------------------------------------------------------

    def get_events(self) -> list[dict[str, Any]]:
        """Return a copy of in-memory buffered events."""
        with self._lock:
            return list(self._buffer)

    def clear_buffer(self) -> None:
        """Clear the in-memory buffer."""
        with self._lock:
            self._buffer.clear()

    @property
    def path(self) -> Optional[str]:
        """Return the audit file path (or ``None``)."""
        return self._path
