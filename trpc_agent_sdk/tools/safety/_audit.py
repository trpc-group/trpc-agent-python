# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logging for the Tool Script Safety Guard.

Writes JSON-lines audit events to a configurable file path.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ._types import Decision
from ._types import RiskLevel
from ._types import SafetyReport
from ._types import ScanTarget
from ._types import ScriptLanguage


@dataclass
class AuditEvent:
    """A structured, auditable record of a tool safety decision."""

    tool_name: str
    decision: Decision
    risk_level: RiskLevel
    duration_ms: int
    blocked: bool
    sanitized: bool
    target: ScanTarget
    language: ScriptLanguage
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rule_ids: List[str] = field(default_factory=list)
    script_path: Optional[str] = None
    trace_attributes: Dict[str, Any] = field(default_factory=dict)


class AuditLogger:
    """Records safety scan results as JSON-lines audit events."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    @classmethod
    def from_report(cls, report: SafetyReport) -> AuditEvent:
        """Convert a SafetyReport into an AuditEvent."""
        return AuditEvent(
            timestamp=report.timestamp,
            tool_name=report.tool_name,
            decision=report.decision,
            risk_level=report.risk_level,
            rule_ids=report.rule_ids,
            duration_ms=report.duration_ms,
            blocked=report.blocked,
            sanitized=report.sanitized,
            target=report.target,
            language=report.language,
            trace_attributes=report.telemetry_attributes,
        )

    def record(self, report: SafetyReport) -> AuditEvent:
        """Create an audit event from a report and append it as a JSON line.

        Creates parent directories if they do not exist.
        """
        event = self.from_report(report)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
        return event
