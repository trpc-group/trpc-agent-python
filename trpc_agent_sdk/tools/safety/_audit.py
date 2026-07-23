# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit event helpers for tool script safety scans."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from ._types import SafetyDecision
from ._types import ToolSafetyReport
from ._types import enum_value


def build_tool_safety_audit_event(
    report: ToolSafetyReport,
    *,
    tool_name: str | None = None,
    blocked: bool | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable audit event for a scan report."""
    rule_ids = [finding.rule_id for finding in report.findings]
    primary_rule_id = str(report.telemetry_attributes.get("tool.safety.rule_id") or (rule_ids[0] if rule_ids else ""))
    blocked_value = blocked
    if blocked_value is None:
        blocked_value = enum_value(report.decision) != SafetyDecision.ALLOW.value

    return {
        "event_type": "tool_safety_scan",
        "schema_version": "1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name or report.tool_name,
        "decision": enum_value(report.decision),
        "risk_level": enum_value(report.risk_level),
        "rule_id": primary_rule_id,
        "rule_ids": rule_ids,
        "duration_ms": round(report.duration_ms, 3),
        "redacted": report.redacted,
        "blocked": bool(blocked_value),
        "finding_count": len(report.findings),
        "policy_name": report.policy_name,
        "policy_version": report.policy_version,
        "telemetry_attributes": report.telemetry_attributes,
    }


class ToolSafetyAuditLogger:
    """Append-only JSONL audit logger for safety decisions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(
        self,
        report: ToolSafetyReport,
        *,
        tool_name: str | None = None,
        blocked: bool | None = None,
    ) -> dict[str, Any]:
        """Append one audit event and return the emitted event."""
        event = build_tool_safety_audit_event(report, tool_name=tool_name, blocked=blocked)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event
