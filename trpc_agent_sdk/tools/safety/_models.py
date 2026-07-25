# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Data models for tool script safety scanning and audit events."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class Decision(str, Enum):
    """Final or recommended script execution decision."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Normalized severity used by reports and monitoring events."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_LEVEL_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class SafetyScanRequest(BaseModel):
    """Inputs available before a script-capable tool is executed."""

    model_config = ConfigDict(extra="forbid")

    script: str
    language: str
    command_args: list[str] = Field(default_factory=list)
    working_directory: Optional[str] = None
    environment: dict[str, str] = Field(default_factory=dict)
    tool_name: str = "unknown"
    tool_metadata: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: Optional[float] = None


class SafetyFinding(BaseModel):
    """A single rule match with redacted evidence and remediation advice."""

    model_config = ConfigDict(extra="forbid")

    category: str
    rule_id: str
    title: str
    risk_level: RiskLevel
    decision: Decision
    evidence: str
    recommendation: str
    line_number: Optional[int] = None
    redacted: bool = False


class SafetyAuditEvent(BaseModel):
    """Compact event intended for JSONL logs and monitoring consumers."""

    model_config = ConfigDict(extra="forbid")

    event_type: str = "tool_safety_scan"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tool_name: str
    decision: Decision
    risk_level: RiskLevel
    rule_id: str
    rule_ids: list[str]
    duration_ms: float
    redacted: bool
    blocked: bool
    script_sha256: str
    policy_version: str


class SafetyReport(BaseModel):
    """Structured result returned for every safety scan."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    risk_level: RiskLevel
    rule_ids: list[str]
    findings: list[SafetyFinding]
    tool_name: str
    language: str
    duration_ms: float
    script_sha256: str
    policy_version: str
    redacted: bool
    scanned_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_audit_event(self, *, blocked: bool) -> SafetyAuditEvent:
        """Build a compact, script-free audit event from this report."""
        return SafetyAuditEvent(
            tool_name=self.tool_name,
            decision=self.decision,
            risk_level=self.risk_level,
            rule_id=self.rule_ids[0] if self.rule_ids else "",
            rule_ids=self.rule_ids,
            duration_ms=self.duration_ms,
            redacted=self.redacted,
            blocked=blocked,
            script_sha256=self.script_sha256,
            policy_version=self.policy_version,
        )
