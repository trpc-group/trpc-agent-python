# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Type definitions for the Tool Script Safety Guard system.

This module defines the core data structures used throughout the safety
scanning pipeline, including:

1. Enumerations:
   - ScriptType: Type of script being scanned (bash / python)
   - RiskCategory: Categories of security risks
   - SafetyDecision: Scanning decision (allow / deny / needs_human_review)
   - RiskLevel: Severity levels for matched risks

2. Data Classes:
   - ScanInput: Input structure for the scanner
   - RuleMatch: A single rule match with evidence and recommendation
   - SafetyReport: Structured report for a complete scan operation
   - AuditEvent: Structured audit log entry for monitoring systems
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import IntEnum
from enum import unique
from typing import Optional

# ── Enumerations ──────────────────────────────────────────────────────────


@unique
class ScriptType(IntEnum):
    """Type of script content being scanned."""

    UNKNOWN = 0
    """Unknown or unsupported script type."""

    BASH = 1
    """Bash / shell script content."""

    PYTHON = 2
    """Python script content."""


@unique
class RiskCategory(IntEnum):
    """Categories of security risks detected by the scanner."""

    UNKNOWN = 0
    """Uncategorised risk."""

    DANGEROUS_FILE_OPERATION = 1
    """Recursive delete, overwrite system directory, access ~/.ssh, read .env."""

    NETWORK_EGRESS = 2
    """Curl, wget, requests, aiohttp, socket to non-whitelisted domains."""

    PROCESS_EXECUTION = 3
    """subprocess, os.system, shell pipes, background processes, privilege escalation."""

    DEPENDENCY_INSTALLATION = 4
    """pip install, npm install, apt install — alters the runtime environment."""

    RESOURCE_ABUSE = 5
    """Infinite loop, fork bomb, large file writes, long sleep, excessive concurrency."""

    SENSITIVE_INFO_LEAK = 6
    """Writing API keys, tokens, passwords, or private keys to logs, files, or network requests."""


@unique
class SafetyDecision(IntEnum):
    """Decision outcome after scanning a script."""

    ALLOW = 0
    """Script is safe to execute."""

    DENY = 1
    """Script is blocked — contains prohibited patterns."""

    NEEDS_HUMAN_REVIEW = 2
    """Suspicious patterns found — requires human approval before execution."""


@unique
class RiskLevel(IntEnum):
    """Severity level of a matched risk rule."""

    LOW = 10
    """Low severity — informational, no immediate danger."""

    MEDIUM = 20
    """Medium severity — suspicious but potentially legitimate."""

    HIGH = 30
    """High severity — clearly dangerous, should be blocked."""

    CRITICAL = 40
    """Critical severity — destructive or irreversible damage possible."""


# ── Data Classes ──────────────────────────────────────────────────────────


@dataclass
class ScanInput:
    """Input structure for the script safety scanner.

    Attributes:
        script_content: The raw script content to be scanned.
        script_type: Type of script (bash / python).
        command_line_args: Optional command-line arguments.
        working_directory: Optional working directory for execution.
        env_vars: Optional environment variables dictionary.
        tool_name: Name of the tool that triggered the scan.
        tool_metadata: Optional tool metadata (e.g. tool type, version).
    """

    script_content: str
    script_type: ScriptType
    command_line_args: Optional[list[str]] = None
    working_directory: Optional[str] = None
    env_vars: Optional[dict[str, str]] = None
    tool_name: str = ""
    tool_metadata: Optional[dict[str, str]] = None


@dataclass
class RuleMatch:
    """A single rule match produced by a scanner.

    Attributes:
        rule_id: Unique identifier of the matched rule (e.g. "R001").
        risk_category: Category of the risk.
        risk_level: Severity level of this match.
        evidence: Snippet of the script content that triggered the rule.
        line_number: 1-based line number where the evidence was found.
        recommendation: Suggested action for the user or operator.
        masked: Whether sensitive data in evidence has been redacted.
    """

    rule_id: str
    risk_category: RiskCategory
    risk_level: RiskLevel
    evidence: str
    line_number: int
    recommendation: str
    masked: bool = False


@dataclass
class SafetyReport:
    """Structured report for a complete scan operation.

    Attributes:
        decision: Overall decision after scanning.
        risk_level: Highest risk level among all matched rules.
        matches: List of individual rule matches (empty if none).
        tool_name: Name of the tool that triggered the scan.
        script_type: Type of script content scanned.
        script_summary: Truncated preview of the script (first 200 chars).
        scan_duration_ms: Time taken to complete the scan in milliseconds.
        timestamp: ISO-8601 timestamp of when the scan was performed.
        policy_version: Version identifier of the policy file used.
    """

    decision: SafetyDecision
    risk_level: RiskLevel
    matches: list[RuleMatch] = field(default_factory=list)
    tool_name: str = ""
    script_type: ScriptType = ScriptType.UNKNOWN
    script_summary: str = ""
    scan_duration_ms: float = 0.0
    timestamp: str = ""
    policy_version: str = ""

    @property
    def is_blocked(self) -> bool:
        """Whether the script was blocked from execution."""
        return self.decision == SafetyDecision.DENY

    @property
    def needs_review(self) -> bool:
        """Whether the script requires human review."""
        return self.decision == SafetyDecision.NEEDS_HUMAN_REVIEW

    @property
    def is_allowed(self) -> bool:
        """Whether the script was allowed to execute."""
        return self.decision == SafetyDecision.ALLOW

    @property
    def match_count(self) -> int:
        """Number of rules matched during scanning."""
        return len(self.matches)

    def to_dict(self) -> dict:
        """Convert the report to a JSON-serializable dictionary."""
        return {
            "decision":
            self.decision.name,
            "risk_level":
            self.risk_level.name,
            "matches": [{
                "rule_id": m.rule_id,
                "risk_category": m.risk_category.name,
                "risk_level": m.risk_level.name,
                "evidence": m.evidence,
                "line_number": m.line_number,
                "recommendation": m.recommendation,
                "masked": m.masked,
            } for m in self.matches],
            "tool_name":
            self.tool_name,
            "script_type":
            self.script_type.name,
            "script_summary":
            self.script_summary,
            "scan_duration_ms":
            self.scan_duration_ms,
            "timestamp":
            self.timestamp,
            "policy_version":
            self.policy_version,
        }


@dataclass
class AuditEvent:
    """A structured audit log entry for monitoring systems.

    Each entry records one safety scan event and is designed to be
    consumable by log aggregators (e.g. ELK, Loki) and compatible
    with OpenTelemetry span attributes.

    Attributes:
        tool_name: Name of the tool being scanned.
        decision: The safety decision made.
        risk_level: Highest risk level detected.
        rule_id: Comma-separated list of matched rule IDs.
        scan_duration_ms: Time taken to scan in milliseconds.
        masked: Whether sensitive data was redacted.
        blocked: Whether execution was intercepted.
        timestamp: ISO-8601 timestamp of the event.
        script_type: Type of script scanned.
    """

    tool_name: str
    decision: str
    risk_level: str
    rule_id: str
    scan_duration_ms: float
    masked: bool
    blocked: bool
    timestamp: str
    script_type: str = ""

    def to_dict(self) -> dict:
        """Convert the audit event to a JSON-serializable dictionary."""
        return {
            "tool_name": self.tool_name,
            "decision": self.decision,
            "risk_level": self.risk_level,
            "rule_id": self.rule_id,
            "scan_duration_ms": self.scan_duration_ms,
            "masked": self.masked,
            "blocked": self.blocked,
            "timestamp": self.timestamp,
            "script_type": self.script_type,
        }

    def to_otel_attributes(self) -> dict[str, str]:
        """Convert to OpenTelemetry span attribute key-value pairs.

        These attributes can be set on a span via::

            span.set_attributes(audit_event.to_otel_attributes())
        """
        return {
            "tool.safety.decision": self.decision,
            "tool.safety.risk_level": self.risk_level,
            "tool.safety.rule_id": self.rule_id,
            "tool.safety.blocked": str(self.blocked),
            "tool.safety.masked": str(self.masked),
            "tool.safety.duration_ms": str(self.scan_duration_ms),
        }
