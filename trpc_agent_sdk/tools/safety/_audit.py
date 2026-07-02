# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit event helpers for tool script safety decisions."""

from __future__ import annotations

import json
from pathlib import Path

from ._types import AuditEvent
from ._types import SafetyReport


def build_audit_event(report: SafetyReport) -> AuditEvent:
    """Build a monitoring-friendly audit event from a safety report."""
    return AuditEvent(
        scan_id=report.scan_id,
        timestamp=report.timestamp,
        tool_name=report.tool_name,
        decision=report.decision,
        risk_level=report.risk_level,
        rule_ids=[finding.rule_id for finding in report.findings],
        elapsed_ms=report.elapsed_ms,
        sanitized=report.sanitized,
        blocked=report.blocked,
        trace_attributes=report.telemetry_attributes,
    )


def write_audit_event(path: str | Path, report: SafetyReport) -> AuditEvent:
    """Append one JSONL audit event to path."""
    event = build_audit_event(report)
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return event
