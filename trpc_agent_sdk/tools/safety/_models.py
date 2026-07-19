# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data models for the Tool Script Safety Guard.

Defines the core data structures used throughout the safety scanning
pipeline: decisions, risk levels, findings, reports and audit events.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Optional


class Decision(str, Enum):
    """Final safety decision for a scanned script."""

    ALLOW = "allow"
    """Script is safe to execute."""

    DENY = "deny"
    """Script must not be executed — high-risk pattern detected."""

    NEEDS_HUMAN_REVIEW = "needs_human_review"
    """Script contains suspicious patterns that a human should review."""


class RiskLevel(str, Enum):
    """Severity of a detected risk."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def max(cls, *levels: "RiskLevel") -> "RiskLevel":
        """Return the highest risk level among the given levels."""
        order = {
            cls.NONE: 0,
            cls.LOW: 1,
            cls.MEDIUM: 2,
            cls.HIGH: 3,
            cls.CRITICAL: 4,
        }
        if not levels:
            return cls.NONE
        return max(levels, key=lambda lv: order.get(lv, 0))


class ScriptType(str, Enum):
    """Type of script being scanned."""

    PYTHON = "python"
    BASH = "bash"
    UNKNOWN = "unknown"


class RiskCategory(str, Enum):
    """Categories of risk the guard can detect."""

    DANGEROUS_FILE_OPS = "dangerous_file_ops"
    """Recursive deletion, system directory access, credential file reads."""

    NETWORK_EGRESS = "network_egress"
    """Outbound network calls to non-whitelisted domains."""

    PROCESS_SYSTEM = "process_system"
    """Subprocess, os.system, shell pipes, privilege escalation."""

    DEPENDENCY_INSTALL = "dependency_install"
    """pip / npm / apt install that mutates the runtime environment."""

    RESOURCE_ABUSE = "resource_abuse"
    """Infinite loops, fork bombs, huge writes, long sleeps."""

    SECRET_LEAK = "secret_leak"
    """API keys, tokens, passwords written to logs, files or network."""


@dataclass
class Finding:
    """A single risk detected during scanning.

    Attributes:
        rule_id: Unique identifier of the rule that triggered.
        category: Risk category (see :class:`RiskCategory`).
        risk_level: Severity of this finding.
        decision: Recommended decision for this finding.
        description: Human-readable explanation.
        evidence: The code snippet or matched text that triggered the rule.
        line_number: 1-based line number where the risk was found, if known.
        recommendation: Suggested remediation action.
    """

    rule_id: str
    category: str
    risk_level: RiskLevel
    decision: Decision
    description: str
    evidence: str
    line_number: Optional[int] = None
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "risk_level": self.risk_level.value,
            "decision": self.decision.value,
            "description": self.description,
            "evidence": self.evidence,
            "line_number": self.line_number,
            "recommendation": self.recommendation,
        }


@dataclass
class SafetyReport:
    """Structured result of a safety scan.

    Attributes:
        tool_name: Name of the tool that would execute the script.
        script_type: Detected type of the script (python / bash).
        decision: Aggregate decision (worst-case across all findings).
        risk_level: Aggregate risk level (highest across all findings).
        findings: List of all findings, ordered by severity.
        scan_duration_ms: Time spent scanning in milliseconds.
        script_hash: SHA-256 hash of the scanned script (for dedup).
        sanitized: Whether sensitive values were redacted from evidence.
        timestamp: ISO-8601 timestamp of the scan.
        summary: Short human-readable summary.
    """

    tool_name: str
    script_type: ScriptType
    decision: Decision
    risk_level: RiskLevel
    findings: list[Finding] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    script_hash: str = ""
    sanitized: bool = False
    timestamp: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "script_type": self.script_type.value,
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "findings": [f.to_dict() for f in self.findings],
            "scan_duration_ms": round(self.scan_duration_ms, 3),
            "script_hash": self.script_hash,
            "sanitized": self.sanitized,
            "timestamp": self.timestamp,
            "summary": self.summary,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class AuditEvent:
    """A single line in the JSONL audit log.

    Attributes:
        timestamp: ISO-8601 timestamp.
        tool_name: Name of the tool.
        decision: Final decision.
        risk_level: Final risk level.
        rule_ids: IDs of all rules that triggered.
        scan_duration_ms: Scan duration in ms.
        sanitized: Whether evidence was redacted.
        blocked: Whether execution was intercepted (denied / review).
        script_hash: SHA-256 hash of the script.
        script_type: Type of the script.
    """

    timestamp: str
    tool_name: str
    decision: str
    risk_level: str
    rule_ids: list[str] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    sanitized: bool = False
    blocked: bool = False
    script_hash: str = ""
    script_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def compute_script_hash(script: str) -> str:
    """Compute a SHA-256 hash of the script content."""
    return hashlib.sha256(script.encode("utf-8")).hexdigest()
