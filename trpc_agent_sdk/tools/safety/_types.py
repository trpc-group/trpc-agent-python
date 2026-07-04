# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared types for tool script safety scanning."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any


class Decision(str, Enum):
    """Safety decision for a script or finding."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Risk severity level."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


@dataclass
class RiskFinding:
    """A single safety finding produced by a scanner rule."""

    rule_id: str
    risk_type: str
    risk_level: RiskLevel
    decision: Decision
    evidence: str
    recommendation: str
    message: str = ""
    line: int | None = None
    column: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "rule_id": self.rule_id,
            "risk_type": self.risk_type,
            "risk_level": self.risk_level.value,
            "decision": self.decision.value,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "metadata": self.metadata,
        }


@dataclass
class ToolScriptScanRequest:
    """Input to the safety scanner."""

    script: str
    language: str
    command_args: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    tool_name: str = "unknown_tool"
    tool_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SafetyReport:
    """Structured report for a completed safety scan."""

    scan_id: str
    timestamp: str
    decision: Decision
    risk_level: RiskLevel
    findings: list[RiskFinding]
    tool_name: str
    language: str
    elapsed_ms: float
    sanitized: bool
    blocked: bool
    summary: str
    telemetry_attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "scan_id": self.scan_id,
            "timestamp": self.timestamp,
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "findings": [finding.to_dict() for finding in self.findings],
            "tool_name": self.tool_name,
            "language": self.language,
            "elapsed_ms": self.elapsed_ms,
            "sanitized": self.sanitized,
            "blocked": self.blocked,
            "summary": self.summary,
            "telemetry_attributes": self.telemetry_attributes,
        }

    def set_blocked(self, blocked: bool) -> None:
        """Set whether the scan result should block execution."""
        self.blocked = blocked


@dataclass
class AuditEvent:
    """Sanitized audit event written as one JSONL row per scan."""

    scan_id: str
    timestamp: str
    tool_name: str
    decision: Decision
    risk_level: RiskLevel
    rule_ids: list[str]
    elapsed_ms: float
    sanitized: bool
    blocked: bool
    trace_attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "scan_id": self.scan_id,
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "rule_ids": self.rule_ids,
            "elapsed_ms": self.elapsed_ms,
            "sanitized": self.sanitized,
            "blocked": self.blocked,
            "trace_attributes": self.trace_attributes,
        }


def aggregate_decision(findings: list[RiskFinding]) -> Decision:
    """Aggregate finding decisions into a report decision."""
    if any(finding.decision == Decision.DENY for finding in findings):
        return Decision.DENY
    if any(finding.decision == Decision.NEEDS_HUMAN_REVIEW for finding in findings):
        return Decision.NEEDS_HUMAN_REVIEW
    return Decision.ALLOW


def max_risk_level(findings: list[RiskFinding]) -> RiskLevel:
    """Return the maximum risk level across findings."""
    if not findings:
        return RiskLevel.NONE
    return max((finding.risk_level for finding in findings), key=lambda level: _RISK_ORDER[level])
