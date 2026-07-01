# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Audit logging and OpenTelemetry span reporting for safety scans.

Writes one JSONL line per scan decision to an audit file, and sets the
reserved ``tool.safety.*`` span attributes on the current OTel span when
OpenTelemetry is configured in the host process.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from typing import Optional

from .types import SafetyReport


class AuditLogger:
    """Append structured audit events to a JSONL file."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None

    def log(self, report: SafetyReport, *, script_path: Optional[str] = None, intercepted: bool = False) -> dict[str, Any]:
        """Emit one audit record. Safe to call when *path* is None (no-op)."""
        record = self._build_record(report, script_path=script_path, intercepted=intercepted)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        _emit_telemetry(report)
        return record

    @staticmethod
    def _build_record(report: SafetyReport, *, script_path: Optional[str], intercepted: bool) -> dict[str, Any]:
        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
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
    """Set ``tool.safety.*`` span attributes on the current OTel span.

    Public alias of the internal helper; safe to call when OTel is absent.
    """
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
        # Telemetry must never break the safety path.
        return
