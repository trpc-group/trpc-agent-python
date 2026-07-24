# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logging for the Tool Script Safety Guard.

Writes structured audit events as JSONL (one JSON object per line).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from datetime import timezone
from pathlib import Path

from ._types import AuditEvent
from ._types import ScanReport


class SafetyAuditLogger:
    """Logs safety scan events to a JSONL file."""

    def __init__(self, output_path: str = "tool_safety_audit.jsonl"):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(self, event: AuditEvent):
        with open(self.output_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def log(
        self,
        report: ScanReport,
        tool_name: str,
        script_hash: str,
        sanitized: bool = False,
    ):
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            tool_name=tool_name,
            decision=report.decision.value,
            risk_level=report.risk_level.value if report.risk_level else None,
            rule_ids=[f.rule_id for f in report.findings],
            scan_duration_ms=report.scan_duration_ms,
            sanitized=sanitized,
            intercepted=(report.decision.value == "deny"),
            script_hash=script_hash,
        )
        self.log_event(event)
