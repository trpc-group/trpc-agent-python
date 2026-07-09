# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core types for the tool script safety guard.

Decision / RiskLevel mirror the Go reference (trpc-agent-go/tool/safety);
Decision is extended with NEEDS_REVIEW to satisfy issue #90's three-state
requirement.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import IntEnum


class Decision(IntEnum):
    """Outcome of a safety scan. Mirrors Go Decision, extended."""

    UNDECIDED = 0
    ALLOW = 1
    DENY = 2
    NEEDS_REVIEW = 3


class RiskLevel(IntEnum):
    """Severity of a detected risk. Mirrors Go RiskLevel."""

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class Finding:
    """A single rule hit produced by a scanner."""

    rule_id: str
    risk_level: RiskLevel
    rule_decision: Decision
    evidence: str
    recommendation: str
    language: str = "python"


@dataclass
class SafetyReport:
    """Aggregated scan result consumed by integrations and audit."""

    decision: Decision
    risk_level: RiskLevel
    findings: list[Finding] = field(default_factory=list)
    recommendation: str = ""
    scan_duration_ms: int = 0
    sanitized: bool = False
