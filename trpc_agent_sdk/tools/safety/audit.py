# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logging and OpenTelemetry emission for the Tool Script Safety Guard.

``AuditLogger`` appends one JSON object per scan to a ``.jsonl`` file (the
post-execution leg of the defence timeline). ``emit_safety_span`` mirrors the
decision onto the current OTel span as ``tool.safety.*`` attributes, degrading
silently when OpenTelemetry is unavailable or no span is active.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Optional

from .models import RiskFinding
from .models import RiskLevel
from .models import SafetyReport

# Environment variable for the default audit file path used by the filter /
# wrappers when none is supplied explicitly.
ENV_AUDIT_PATH = "TOOL_SAFETY_AUDIT_PATH"

# OpenTelemetry is a hard dependency of the SDK, but we still guard the import so
# the guard's core stays usable in trimmed-down environments.
try:  # pragma: no cover - exercised indirectly
    from opentelemetry import trace as _otel_trace
except Exception:  # pylint: disable=broad-except
    _otel_trace = None  # type: ignore


def _primary_rule_id(findings: list[RiskFinding]) -> Optional[str]:
    """The rule id of the most severe finding (stable tie-break by order)."""
    if not findings:
        return None
    top = max(findings, key=lambda f: f.risk_level.order)
    return top.rule_id


def build_audit_record(report: SafetyReport, blocked: bool) -> dict:
    """Build the auditable record for one scan.

    Contains every field required by the design: timestamp, tool_name,
    decision, risk_level, rule_id, scan_duration_ms, redacted, blocked.
    """
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": report.tool_name,
        "language": report.language,
        "decision": report.decision.value,
        "risk_level": report.risk_level.value,
        "rule_id": _primary_rule_id(report.findings),
        "rule_ids": [f.rule_id for f in report.findings],
        "finding_count": len(report.findings),
        "scan_duration_ms": round(report.scan_duration_ms, 3),
        "redacted": report.redacted,
        "blocked": blocked,
    }


class AuditLogger:
    """Append-only JSONL audit sink. Best-effort: never raises into tool flow."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, report: SafetyReport, blocked: bool) -> dict:
        """Write one audit record and return it. Swallows IO errors."""
        record = build_audit_record(report, blocked)
        if self.path is None:
            return record
        line = json.dumps(record, ensure_ascii=False)
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError:
            # Auditing must not break tool execution; the caller already has the
            # decision. Failure here is non-fatal.
            pass
        return record


# Span attribute keys (stable, documented in the README).
ATTR_DECISION = "tool.safety.decision"
ATTR_RISK_LEVEL = "tool.safety.risk_level"
ATTR_RULE_ID = "tool.safety.rule_id"
ATTR_RULE_IDS = "tool.safety.rule_ids"
ATTR_BLOCKED = "tool.safety.blocked"
ATTR_REDACTED = "tool.safety.redacted"


def emit_safety_span(report: SafetyReport, blocked: bool) -> bool:
    """Attach safety attributes to the current OTel span.

    Returns True if attributes were written. Degrades silently (returns False)
    when OpenTelemetry is missing or there is no recording span.
    """
    if _otel_trace is None:
        return False
    try:
        span = _otel_trace.get_current_span()
        if span is None or not span.is_recording():
            return False
        span.set_attribute(ATTR_DECISION, report.decision.value)
        span.set_attribute(ATTR_RISK_LEVEL, report.risk_level.value)
        rule_id = _primary_rule_id(report.findings)
        if rule_id:
            span.set_attribute(ATTR_RULE_ID, rule_id)
        span.set_attribute(ATTR_RULE_IDS, [f.rule_id for f in report.findings])
        span.set_attribute(ATTR_BLOCKED, blocked)
        span.set_attribute(ATTR_REDACTED, report.redacted)
        return True
    except Exception:  # pylint: disable=broad-except
        return False
