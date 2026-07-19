# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core data types for the Tool Script Safety Guard."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any


class Decision(str, Enum):
    """Final risk decision for a scanned script."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Severity bucket for a finding or the aggregate report."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SafetyFinding:
    """A single rule hit."""

    rule_id: str
    rule_name: str
    risk_type: str
    risk_level: RiskLevel
    evidence: str
    line: int | None = None
    recommendation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanInput:
    """Inputs presented to the scanner."""

    script: str
    language: str = ""
    args: list[str] | None = None
    workdir: str | None = None
    env: dict[str, str] | None = None
    tool_name: str = "unknown"
    tool_description: str | None = None


@dataclass
class SafetyReport:
    """Structured scan result."""

    decision: Decision
    risk_level: RiskLevel
    findings: list[SafetyFinding]
    rule_ids: list[str]
    scanner_version: str
    scan_duration_ms: float
    sanitized: bool
    blocked: bool
    tool_name: str
    language: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for report files and audit."""
        return {
            "decision":
            self.decision.value,
            "risk_level":
            self.risk_level.value,
            "findings": [{
                "rule_id": f.rule_id,
                "rule_name": f.rule_name,
                "risk_type": f.risk_type,
                "risk_level": f.risk_level.value,
                "evidence": f.evidence,
                "line": f.line,
                "recommendation": f.recommendation,
                "metadata": f.metadata,
            } for f in self.findings],
            "rule_ids":
            self.rule_ids,
            "scanner_version":
            self.scanner_version,
            "scan_duration_ms":
            round(self.scan_duration_ms, 3),
            "sanitized":
            self.sanitized,
            "blocked":
            self.blocked,
            "tool_name":
            self.tool_name,
            "language":
            self.language,
        }


_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}

_DECISION_ORDER = {
    Decision.ALLOW: 0,
    Decision.NEEDS_HUMAN_REVIEW: 1,
    Decision.DENY: 2,
}


def max_risk_level(levels: list[RiskLevel]) -> RiskLevel:
    """Return the highest severity among *levels*; NONE when empty."""
    if not levels:
        return RiskLevel.NONE
    return max(levels, key=lambda lv: _RISK_ORDER[lv])


def risk_order(level: RiskLevel) -> int:
    """Numeric rank for a risk level."""
    return _RISK_ORDER[level]


def decision_rank(decision: Decision) -> int:
    """Numeric rank for a decision (ALLOW < NEEDS_HUMAN_REVIEW < DENY).

    Used by multi-block aggregation so that a NEEDS_HUMAN_REVIEW block
    outweighs an ALLOW block instead of being silently masked.
    """
    return _DECISION_ORDER[decision]
