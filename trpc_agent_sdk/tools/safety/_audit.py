# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logging for the Tool Script Safety Guard.

Writes JSON-lines audit events to a configurable file path.  Thread-safe
via a module-level lock.  I/O failures are swallowed — audit plumbing
never blocks tool execution.
"""

from __future__ import annotations

import json
import threading
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

_AUDIT_LOCK = threading.Lock()


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
    """Records safety scan results as JSON-lines audit events.

    Thread-safe via a module-level lock.  When *path* is None, ``record()``
    is a no-op (no file written).
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = Path(path) if path else None

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
        """Create an audit event and append it as a JSON line (if path is set).

        Audit I/O failures are swallowed — they never block tool execution.
        """
        event = self.from_report(report)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n"
                with _AUDIT_LOCK:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(line)
                        fh.flush()
            except Exception:
                pass
        return event
