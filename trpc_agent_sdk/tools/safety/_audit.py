# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit trail and OpenTelemetry emission for safety decisions.

Every decision is emitted at a single point in two agent-native ways
(borrowed from Codex's philosophy of co-locating audit and telemetry):

- a structured JSON line appended to an audit log (``tool_safety_audit.jsonl``)
- OpenTelemetry span attributes on the current tool span:
  ``tool.safety.decision`` / ``tool.safety.risk_level`` / ``tool.safety.rule_id``

Both are best-effort and never raise into the execution path — auditing must
not break the tool it protects.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Optional

from opentelemetry import trace

from ._types import ScanReport

# Span attribute keys required by the issue.
SPAN_ATTR_DECISION = "tool.safety.decision"
SPAN_ATTR_RISK_LEVEL = "tool.safety.risk_level"
SPAN_ATTR_RULE_ID = "tool.safety.rule_id"
SPAN_ATTR_BLOCKED = "tool.safety.blocked"


def set_span_attributes(report: ScanReport) -> None:
    """Record the safety decision on the current OpenTelemetry span.

    Args:
        report: The scan report whose decision should be surfaced as span
            attributes. Failures are swallowed so telemetry never breaks
            execution.
    """
    try:
        span = trace.get_current_span()
        span.set_attribute(SPAN_ATTR_DECISION, report.decision.value)
        span.set_attribute(SPAN_ATTR_RISK_LEVEL, report.risk_level.value)
        # OTel accepts a homogeneous sequence; a list is more idiomatic than a
        # comma-joined string and keeps each rule id queryable.
        span.set_attribute(SPAN_ATTR_RULE_ID, report.rule_ids())
        span.set_attribute(SPAN_ATTR_BLOCKED, report.blocked)
    except Exception:  # pragma: no cover - telemetry must never break execution
        pass


class SafetyAuditLogger:
    """Append-only JSONL audit sink for safety decisions."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        """Create an audit logger.

        Args:
            path: Destination JSONL file. When ``None`` events are still built
                and returned (and emitted to the span) but not persisted, which
                keeps the logger side-effect-free in tests.
        """
        self._path = Path(path) if path is not None else None

    @property
    def path(self) -> Optional[Path]:
        """Return the audit file path, if any."""
        return self._path

    def build_event(self, report: ScanReport, *, blocked: bool) -> dict[str, Any]:
        """Build the structured audit event for a scan report.

        Args:
            report: The scan report to summarise.
            blocked: Whether execution was actually prevented.

        Returns:
            A JSON-serialisable audit event.
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": report.tool_name,
            "language": report.language.value,
            "decision": report.decision.value,
            "risk_level": report.risk_level.value,
            "rule_ids": report.rule_ids(),
            "num_hits": len(report.hits),
            "blocked": blocked,
            "redacted": report.redacted,
            "duration_ms": report.duration_ms,
        }

    def record(self, report: ScanReport, *, blocked: bool) -> dict[str, Any]:
        """Emit span attributes and append the audit event to the log.

        Args:
            report: The scan report to record.
            blocked: Whether execution was actually prevented.

        Returns:
            The audit event that was recorded.
        """
        set_span_attributes(report)
        event = self.build_event(report, blocked=blocked)
        if self._path is not None:
            self._append(event)
        return event

    def _append(self, event: dict[str, Any]) -> None:
        """Append one event as a JSON line, creating parent dirs as needed."""
        import json

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:  # pragma: no cover - disk errors must not break execution
            pass
