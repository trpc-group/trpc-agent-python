# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Structured audit logging + OpenTelemetry span attributes for safety scans.

Issue #90 requires an audit log / monitoring event for every safety decision,
containing at least: tool name, decision, risk level, rule id, duration,
sanitized flag, and whether execution was intercepted. It also requires
OpenTelemetry span attributes (tool.safety.*) when OTel is enabled.

This module provides:
- AuditRecord: the auditable event dataclass (jsonl-serializable).
- write_audit(): append one JSON line to a .jsonl audit log (best-effort).
- emit_otel(): attach tool.safety.* attributes to the current OTel span.
- record_safety_decision(): convenience that does both.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.tools.safety._types import SafetyReport


def _default_audit_path() -> str:
    """Resolve the audit log path: arg > env > default."""
    return os.environ.get("TRPC_AGENT_TOOL_SAFETY_AUDIT", "tool_safety_audit.jsonl")


@dataclass
class AuditRecord:
    """One auditable safety-decision event (satisfies issue #90 fields)."""

    timestamp: str
    """ISO-8601 UTC timestamp of the decision."""

    tool_name: str
    """Name of the tool / executor the script was submitted to."""

    language: str
    """Detected/declared script language ("python" | "bash" | "auto")."""

    decision: str
    """Final decision name (ALLOW | DENY | NEEDS_REVIEW)."""

    risk_level: str
    """Highest risk level among findings (NONE | LOW | MEDIUM | HIGH)."""

    rule_ids: list[str]
    """Sorted unique rule_ids that fired."""

    scan_duration_ms: int
    """Scan wall-clock duration in milliseconds."""

    sanitized: bool
    """Whether evidence was redacted before reporting."""

    intercepted: bool
    """Whether execution was blocked (not handed to the real tool/executor)."""

    recommendation: str = ""
    """Human-readable guidance for the decision."""


def build_audit_record(
    report: SafetyReport,
    *,
    tool_name: str,
    language: str,
    intercepted: bool,
) -> AuditRecord:
    """Build an AuditRecord from a scan report plus integration context."""
    return AuditRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tool_name=tool_name or "unknown",
        language=language,
        decision=report.decision.name,
        risk_level=report.risk_level.name,
        rule_ids=sorted({f.rule_id
                         for f in report.findings}),
        scan_duration_ms=report.scan_duration_ms,
        sanitized=bool(report.sanitized),
        intercepted=bool(intercepted),
        recommendation=report.recommendation,
    )


def write_audit(record: AuditRecord, path: Optional[str] = None) -> Path:
    """Append one JSON line to the audit log. Best-effort: never raises.

    Path resolution: explicit arg > env TRPC_AGENT_TOOL_SAFETY_AUDIT > default.
    Audit must not break the protected call path on I/O failure.
    """
    target = Path(path) if path else Path(_default_audit_path())
    try:
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    except OSError:
        pass
    return target


def emit_otel(
    report: SafetyReport,
    *,
    tool_name: str,
    intercepted: bool,
) -> None:
    """Attach tool.safety.* attributes to the current OTel span (if recording).

    No-op when OpenTelemetry is not installed or no span is active, so this is
    safe to call unconditionally. These are the attributes issue #90 reserves:
    tool.safety.decision / risk_level / rule_id / scan_duration_ms /
    sanitized / blocked (+ tool_name).
    """
    try:
        from opentelemetry import trace
    except ImportError:
        return
    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return
    rule_ids = ",".join(sorted({f.rule_id for f in report.findings}))
    span.set_attribute("tool.safety.decision", report.decision.name)
    span.set_attribute("tool.safety.risk_level", report.risk_level.name)
    span.set_attribute("tool.safety.rule_id", rule_ids)
    span.set_attribute("tool.safety.scan_duration_ms", report.scan_duration_ms)
    span.set_attribute("tool.safety.sanitized", bool(report.sanitized))
    span.set_attribute("tool.safety.blocked", bool(intercepted))
    span.set_attribute("tool.safety.tool_name", tool_name or "unknown")


def record_safety_decision(
    report: SafetyReport,
    *,
    tool_name: str,
    language: str,
    intercepted: bool,
    audit_path: Optional[str] = None,
) -> AuditRecord:
    """Build the record, append the jsonl audit line, and emit OTel attributes."""
    record = build_audit_record(
        report,
        tool_name=tool_name,
        language=language,
        intercepted=intercepted,
    )
    write_audit(record, audit_path)
    emit_otel(report, tool_name=tool_name, intercepted=intercepted)
    return record
