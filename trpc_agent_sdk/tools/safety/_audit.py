# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logger for the Tool Script Safety Guard.

Writes JSONL (one JSON object per line) audit events so that SIEM / log
aggregation systems can ingest them easily.

Usage::

    from trpc_agent_sdk.tools.safety import AuditLogger

    logger = AuditLogger("/var/log/tool_safety_audit.jsonl")
    logger.log_event(report)
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from ._types import SafetyAuditEvent
from ._types import SafetyScanReport

_AUDIT_LOGGER = logging.getLogger("trpc_agent_sdk.tools.safety.audit")

# Per-path locks for thread-safe AND process-safe concurrent writes via
# fcntl.flock.  On platforms without fcntl (Windows), falls back to
# threading.Lock which is only thread-safe.
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_LOCK = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """Return (and cache) a threading.Lock for the given JSONL path.

    Paths are normalised via ``os.path.realpath`` so that symlinks and
    relative paths map to the same lock.  The cache grows with each unique
    path; in practice audit paths are bounded (one per deployment).
    """
    real = os.path.realpath(path)
    with _FILE_LOCKS_LOCK:
        if real not in _FILE_LOCKS:
            _FILE_LOCKS[real] = threading.Lock()
        return _FILE_LOCKS[real]


class AuditLogger:
    """Writes structured audit events to a JSONL file and/or stdout.

    Args:
        output_path: Path to the JSONL file. If ``None``, events are only
                     emitted via the module logger.
        also_log: If ``True``, also emit each event via ``logging.info``.
    """

    def __init__(self, output_path: Optional[str] = None, *, also_log: bool = True) -> None:
        self._output_path = output_path
        self._also_log = also_log
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            # Ensure the lock exists
            _get_file_lock(str(output_path))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_event(self, report: SafetyScanReport) -> SafetyAuditEvent:
        """Convert *report* to an audit event and persist it.

        Unlike the previous version, writes to the JSONL file are now
        **thread-safe** — concurrent calls from multiple tool invocations
        will not interleave JSON lines.

        Args:
            report: The scan report to audit.

        Returns:
            The ``SafetyAuditEvent`` that was logged.
        """
        event = self._build_event(report)
        line = json.dumps(event.to_dict(), ensure_ascii=False, default=str)

        # File output — thread-safe + process-safe via fcntl.flock
        if self._output_path:
            lock = _get_file_lock(str(self._output_path))
            with lock:
                try:
                    with open(self._output_path, "a", encoding="utf-8") as fh:
                        # Acquire an OS-level advisory lock for cross-process safety
                        try:
                            import fcntl
                            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                        except (ImportError, OSError):
                            pass  # fcntl not available (e.g. Windows) — thread-lock only
                        fh.write(line + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                except OSError as exc:
                    _AUDIT_LOGGER.error("Failed to write audit event: %s", exc)

        # Logger output
        if self._also_log:
            _AUDIT_LOGGER.info("tool_safety_audit: %s", line)

        return event

    def log_events(self, reports: list[SafetyScanReport]) -> list[SafetyAuditEvent]:
        """Batch-log multiple reports."""
        return [self.log_event(r) for r in reports]

    def read_events(self, limit: int = 100) -> list[dict]:
        """Read the most recent audit events from the JSONL file.

        Reads from the tail of the file to avoid loading the entire file
        into memory.  Falls back to a full scan for very small files.

        Args:
            limit: Maximum number of events to return (most recent first).

        Returns:
            List of event dicts, newest first.
        """
        if not self._output_path or not os.path.exists(self._output_path):
            return []
        path = self._output_path
        fsize = os.path.getsize(path)
        if fsize == 0:
            return []
        events: list[dict] = []
        # Estimated bytes per line: average ~200, read 2× to be safe
        read_size = min(fsize, max(limit * 400, 8192))
        try:
            with open(path, "rb") as fh:
                if fsize > read_size:
                    fh.seek(fsize - read_size)
                    # Skip partial first line (may be truncated)
                    fh.readline()
                for line in fh:
                    line = line.decode("utf-8", errors="replace").strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            return []
        return events[-limit:][::-1]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_event(report: SafetyScanReport) -> SafetyAuditEvent:
        return SafetyAuditEvent(
            timestamp=datetime.datetime.fromtimestamp(report.timestamp, tz=datetime.timezone.utc).isoformat(),
            tool_name=report.tool_name,
            decision=report.decision.value,
            risk_level=report.risk_level.value,
            rule_ids=[f.rule_id for f in report.findings],
            scan_id=report.scan_id,
            scan_duration_ms=report.scan_duration_ms,
            sanitized=report.sanitized,
            execution_blocked=report.execution_blocked,
        )
