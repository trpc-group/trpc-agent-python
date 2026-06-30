# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data models for the Tool Script Safety Guard.

This module defines the enums and dataclasses shared across the safety
sub-package. Everything here is pure data with no side effects so it can be
imported from scanners, the engine, filters, wrappers and the CLI without
creating import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Optional


class Decision(str, Enum):
    """Final policy decision for a scanned payload."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskType(str, Enum):
    """The six categories of risk the guard scans for."""

    DANGEROUS_FILE_OP = "dangerous_file_op"
    NETWORK_EGRESS = "network_egress"
    PROCESS_EXEC = "process_exec"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SECRET_LEAK = "secret_leak"


class RiskLevel(str, Enum):
    """Severity of a single finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def order(self) -> int:
        """Numeric severity used for comparisons (higher is more severe)."""
        return _RISK_LEVEL_ORDER[self]


class SuggestedAction(str, Enum):
    """The action a single rule suggests for its match.

    The engine aggregates per-finding actions/levels into a final ``Decision``
    (see ``engine.py``). ``ALLOW`` means the rule is informational only.
    """

    ALLOW = "allow"
    REVIEW = "review"
    DENY = "deny"


_RISK_LEVEL_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class Language(str, Enum):
    """Detected payload language; drives which scanner runs."""

    PYTHON = "python"
    BASH = "bash"
    UNKNOWN = "unknown"


@dataclass
class Evidence:
    """A snippet of the offending payload plus its 1-based line number.

    ``snippet`` may be redacted by the engine before it reaches a report or
    the audit log (see ``redacted`` on :class:`SafetyReport`).
    """

    snippet: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {"snippet": self.snippet, "line": self.line}


@dataclass
class RiskFinding:
    """A single rule hit produced by a scanner."""

    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    evidence: Evidence
    recommendation: str
    suggested_action: SuggestedAction = SuggestedAction.REVIEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "risk_type": self.risk_type.value,
            "risk_level": self.risk_level.value,
            "evidence": self.evidence.to_dict(),
            "recommendation": self.recommendation,
            "suggested_action": self.suggested_action.value,
        }


@dataclass
class SafetyReport:
    """Structured report for one scanned payload.

    Satisfies the acceptance requirement that a report carries ``decision``,
    ``risk_level``, and per-finding ``rule_id`` / ``evidence`` /
    ``recommendation``.
    """

    tool_name: str
    language: str
    decision: Decision
    risk_level: RiskLevel
    findings: list[RiskFinding] = field(default_factory=list)
    redacted: bool = False
    scan_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "language": self.language,
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "redacted": self.redacted,
            "scan_duration_ms": round(self.scan_duration_ms, 3),
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class ScanInput:
    """Everything a scanner needs to inspect one tool invocation.

    ``script`` is the primary payload (a shell command or a code block).
    ``args`` keeps the full tool argument dict so fallback scanning can reach
    secondary string values.
    """

    script: str
    tool_name: str = "unknown"
    language: Language = Language.UNKNOWN
    args: dict[str, Any] = field(default_factory=dict)
    cwd: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
