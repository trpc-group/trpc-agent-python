# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Structured types for tool script safety scanning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Mapping


class SafetyDecision(str, Enum):
    """Decision returned by the safety scanner."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class SafetyRiskLevel(str, Enum):
    """Risk levels used in findings and aggregate reports."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_LEVEL_ORDER = {
    SafetyRiskLevel.NONE.value: 0,
    SafetyRiskLevel.LOW.value: 1,
    SafetyRiskLevel.MEDIUM.value: 2,
    SafetyRiskLevel.HIGH.value: 3,
    SafetyRiskLevel.CRITICAL.value: 4,
}


def risk_level_value(level: SafetyRiskLevel | str) -> int:
    """Return numeric ordering for a risk level."""
    value = level.value if isinstance(level, SafetyRiskLevel) else str(level)
    return RISK_LEVEL_ORDER.get(value, 0)


def max_risk_level(levels: list[SafetyRiskLevel | str]) -> SafetyRiskLevel:
    """Return the highest risk level from a list."""
    if not levels:
        return SafetyRiskLevel.NONE
    max_value = max(risk_level_value(level) for level in levels)
    for level, value in RISK_LEVEL_ORDER.items():
        if value == max_value:
            return SafetyRiskLevel(level)
    return SafetyRiskLevel.NONE


def enum_value(value: Any) -> Any:
    """Convert enum values to JSON-friendly primitive values."""
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass
class ToolSafetyScanRequest:
    """Input to a tool script safety scan."""

    script: str
    language: str = "python"
    command_args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    tool_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def tool_name(self) -> str:
        """Return the tool name when present in metadata."""
        value = self.tool_metadata.get("name") or self.tool_metadata.get("tool_name") or ""
        return str(value)


@dataclass
class ToolSafetyFinding:
    """A single rule hit from the safety scanner."""

    rule_id: str
    risk_type: str
    risk_level: SafetyRiskLevel | str
    message: str
    evidence: str
    recommendation: str
    line_no: int | None = None
    column: int | None = None
    redacted: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "rule_id": self.rule_id,
            "risk_type": self.risk_type,
            "risk_level": enum_value(self.risk_level),
            "message": self.message,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "line_no": self.line_no,
            "column": self.column,
            "redacted": self.redacted,
        }


@dataclass
class ToolSafetyReport:
    """Structured scanner report."""

    decision: SafetyDecision | str
    risk_level: SafetyRiskLevel | str
    findings: list[ToolSafetyFinding]
    duration_ms: float
    language: str
    scanned_at: str
    tool_name: str = ""
    cwd: str | None = None
    policy_name: str = "default"
    policy_version: str = "1"
    blocked: bool = False
    redacted: bool = False
    summary: str = ""
    telemetry_attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        """Return whether the script is allowed to execute."""
        return enum_value(self.decision) == SafetyDecision.ALLOW.value

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "decision": enum_value(self.decision),
            "risk_level": enum_value(self.risk_level),
            "risk_count": len(self.findings),
            "findings": [finding.to_dict() for finding in self.findings],
            "duration_ms": round(self.duration_ms, 3),
            "language": self.language,
            "scanned_at": self.scanned_at,
            "tool_name": self.tool_name,
            "cwd": self.cwd,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "blocked": self.blocked,
            "redacted": self.redacted,
            "summary": self.summary,
            "telemetry_attributes": self.telemetry_attributes,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize the report as JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
