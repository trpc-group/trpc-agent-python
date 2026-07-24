# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logging and OpenTelemetry span reporting for safety scans."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any
from typing import Optional

from ._types import SafetyReport

_logger = logging.getLogger(__name__)

# Process-local lock so concurrent threads do not interleave long JSONL lines.
#
# Cross-process note: this lock does NOT coordinate across OS processes. If
# multiple worker processes write to the same audit_path simultaneously, long
# JSONL lines (above PIPE_BUF, typically 4096 bytes on Linux) may interleave
# or truncate because O_APPEND atomicity is only guaranteed up to PIPE_BUF.
# For multi-process deployments, either (a) give each worker a distinct
# audit_path, (b) funnel audit writes through a single writer process, or
# (c) use fcntl.flock for cross-process exclusion. The current single-process
# design matches the typical Tool/SafetyFilter usage where one agent process
# owns the audit log.
_AUDIT_LOCK = threading.Lock()


class AuditLogger:
    """Append structured audit events to a JSONL file."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None

    def log(
        self,
        report: SafetyReport,
        *,
        script_path: Optional[str] = None,
        intercepted: bool = False,
    ) -> dict[str, Any]:
        """Emit one audit record. Safe to call when *path* is None (no-op).

        Audit I/O failures (unwritable parent, disk full, path is a file, etc.)
        are swallowed and logged as a warning. Callers in the filter / wrapper
        path must never have tool execution blocked by audit plumbing — the
        scan decision already lives in the returned *record*.
        """
        record = self._build_record(report, script_path=script_path, intercepted=intercepted)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(record, ensure_ascii=False) + "\n"
                with _AUDIT_LOCK:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(line)
                        fh.flush()
            except OSError as ex:
                _logger.warning(
                    "safety audit write failed path=%s err=%s",
                    self.path,
                    ex,
                )
        _emit_telemetry(report)
        return record

    @staticmethod
    def _build_record(
        report: SafetyReport,
        *,
        script_path: Optional[str],
        intercepted: bool,
    ) -> dict[str, Any]:
        return {
            # Use UTC with explicit 'Z' suffix so audit timestamps are
            # comparable across containers/CI regardless of process timezone.
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool_name": report.tool_name,
            "decision": report.decision.value,
            "risk_level": report.risk_level.value,
            "rule_ids": report.rule_ids,
            "scan_duration_ms": round(report.scan_duration_ms, 3),
            "sanitized": report.sanitized,
            "intercepted": intercepted,
            "blocked": report.blocked,
            "scanner_version": report.scanner_version,
            "language": report.language,
            "script_path": script_path,
            "findings_count": len(report.findings),
        }


def emit_telemetry(report: SafetyReport) -> None:
    """Set ``tool.safety.*`` span attributes on the current OTel span."""
    _emit_telemetry(report)


def _emit_telemetry(report: SafetyReport) -> None:
    """Best-effort span attribute injection. No-op when OTel is unavailable."""
    try:
        from opentelemetry import trace  # type: ignore
    except ImportError:
        return
    try:
        span = trace.get_current_span()
        if span is None or not getattr(span, "is_recording", lambda: False)():
            return
        span.set_attribute("tool.safety.decision", report.decision.value)
        span.set_attribute("tool.safety.risk_level", report.risk_level.value)
        span.set_attribute("tool.safety.rule_id", ",".join(report.rule_ids))
        span.set_attribute("tool.safety.scan_duration_ms", report.scan_duration_ms)
        span.set_attribute("tool.safety.sanitized", report.sanitized)
        span.set_attribute("tool.safety.blocked", report.blocked)
        span.set_attribute("tool.safety.tool_name", report.tool_name)
    except Exception:  # pylint: disable=broad-except
        return
