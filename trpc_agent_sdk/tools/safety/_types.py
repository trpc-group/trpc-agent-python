# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Types and data models for the Tool Script Safety Guard.

This module defines the core data structures used throughout the safety
system: risk levels, decisions, scan findings, scan reports, metadata about
the tool being scanned, and audit events.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional

# ---------------------------------------------------------------------------
# Helpers — must be defined before RiskLevel class
# ---------------------------------------------------------------------------

_RISK_SEVERITY_MAP: dict[str, int] = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RiskLevel(str, enum.Enum):
    """Severity of a detected risk.

    Values (in increasing severity):
        INFO:     Informational note — no action needed.
        LOW:      Minor concern — allowed by default but logged.
        MEDIUM:   Requires human review before execution.
        HIGH:     Significant danger — denied by default.
        CRITICAL: Severe danger — always denied.
    """
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    # Numeric severity for correct ordering (higher = more severe)
    _severity: int

    def __init__(self, _value: str) -> None:
        self._severity = _RISK_SEVERITY_MAP[_value]

    def __lt__(self, other: "RiskLevel") -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self._severity < other._severity

    def __le__(self, other: "RiskLevel") -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self._severity <= other._severity

    def __gt__(self, other: "RiskLevel") -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self._severity > other._severity

    def __ge__(self, other: "RiskLevel") -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self._severity >= other._severity


class Decision(str, enum.Enum):
    """Execution decision returned by the safety scanner.

    Members:
        ALLOW:                Safe — proceed with execution.
        DENY:                 Unsafe — block execution entirely.
        NEEDS_HUMAN_REVIEW:   Ambiguous — a human must approve before executing.
    """
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskCategory(str, enum.Enum):
    """Well-known risk categories covered by built-in rules."""
    DANGEROUS_FILE_OPS = "dangerous_file_ops"
    NETWORK_EGRESS = "network_egress"
    PROCESS_AND_SYSTEM = "process_and_system"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SENSITIVE_INFO_LEAK = "sensitive_info_leak"


class ScriptType(str, enum.Enum):
    """Script language the scanner should analyse."""
    PYTHON = "python"
    BASH = "bash"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


@dataclass
class SafetyScanInput:
    """All information needed to perform a safety scan.

    Attributes:
        script_content: The raw script or command text.
        script_type: Python, Bash, or unknown (auto-detect).
        command_args: Command-line arguments passed alongside the script.
        working_directory: Target working directory for execution.
        environment_variables: Environment variables that would be set.
        tool_name: Name of the tool / skill that will execute the script.
        tool_description: Description / metadata of the calling tool.
    """
    script_content: str
    script_type: ScriptType = ScriptType.UNKNOWN
    command_args: Optional[list[str]] = None
    working_directory: Optional[str] = None
    environment_variables: Optional[dict[str, str]] = None
    tool_name: str = ""
    tool_description: str = ""
    # Extra metadata that callers may attach
    extra_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


@dataclass
class SafetyFinding:
    """A single risk discovered during the scan.

    Attributes:
        rule_id: Unique identifier of the rule that fired (e.g. 'NET-001').
        category: Risk category the finding belongs to.
        risk_level: Severity of this specific finding.
        evidence: The matching snippet or line(s) from the script.
        message: Human-readable description of the risk.
        recommendation: Suggested remediation steps.
        line_number: 1-based line where the evidence was found (0 = unknown).
        matched_pattern: The regex / pattern that triggered the rule.
        extra: Arbitrary metadata attached by the rule.
    """
    rule_id: str
    category: RiskCategory
    risk_level: RiskLevel
    evidence: str
    message: str
    recommendation: str
    line_number: int = 0
    matched_pattern: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------


@dataclass
class SafetyScanReport:
    """Structured result of a single safety scan.

    Attributes:
        scan_id: Unique identifier for this scan.
        timestamp: UNIX timestamp (float) when the scan was performed.
        tool_name: Name of the scanned tool.
        script_type: Detected or supplied script language.
        script_size_lines: Number of lines in the scanned script.
        decision: Final allow / deny / needs_human_review.
        risk_level: Highest risk level among all findings.
        findings: List of individual risk findings.
        summary: One-sentence summary of the outcome.
        scan_duration_ms: Wall-clock duration of the scan in milliseconds.
        policy_version: Hash or version of the policy used.
        sanitized: Whether secrets in evidence fields have been masked.
        execution_blocked: Whether execution was actually prevented.
    """
    scan_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)
    tool_name: str = ""
    script_type: ScriptType = ScriptType.UNKNOWN
    script_size_lines: int = 0
    decision: Decision = Decision.ALLOW
    risk_level: RiskLevel = RiskLevel.INFO
    findings: list[SafetyFinding] = field(default_factory=list)
    summary: str = ""
    scan_duration_ms: float = 0.0
    policy_version: str = ""
    sanitized: bool = False
    execution_blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a JSON-compatible dictionary."""
        return {
            "scan_id":
            self.scan_id,
            "timestamp":
            self.timestamp,
            "tool_name":
            self.tool_name,
            "script_type":
            self.script_type.value,
            "script_size_lines":
            self.script_size_lines,
            "decision":
            self.decision.value,
            "risk_level":
            self.risk_level.value,
            "summary":
            self.summary,
            "scan_duration_ms":
            self.scan_duration_ms,
            "policy_version":
            self.policy_version,
            "sanitized":
            self.sanitized,
            "execution_blocked":
            self.execution_blocked,
            "findings": [{
                "rule_id": f.rule_id,
                "category": f.category.value,
                "risk_level": f.risk_level.value,
                "message": f.message,
                "evidence": f.evidence,
                "recommendation": f.recommendation,
                "line_number": f.line_number,
                "matched_pattern": f.matched_pattern,
            } for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Audit event model
# ---------------------------------------------------------------------------


@dataclass
class SafetyAuditEvent:
    """A single audit-log entry emitted after every safety scan.

    Designed to be written as one JSON line (JSONL) for easy ingestion by
    log aggregation and SIEM systems.

    Attributes:
        timestamp: ISO-8601 timestamp string.
        tool_name: Name of the scanned tool.
        decision: Final decision.
        risk_level: Highest risk level.
        rule_ids: List of rule IDs that fired.
        scan_id: Correlates with the full SafetyScanReport.
        scan_duration_ms: Scan wall-clock duration.
        sanitized: Whether secrets were masked.
        execution_blocked: Whether execution was blocked.
    """
    timestamp: str = ""
    tool_name: str = ""
    decision: str = ""
    risk_level: str = ""
    rule_ids: list[str] = field(default_factory=list)
    scan_id: str = ""
    scan_duration_ms: float = 0.0
    sanitized: bool = False
    execution_blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "decision": self.decision,
            "risk_level": self.risk_level,
            "rule_ids": self.rule_ids,
            "scan_id": self.scan_id,
            "scan_duration_ms": self.scan_duration_ms,
            "sanitized": self.sanitized,
            "execution_blocked": self.execution_blocked,
        }
