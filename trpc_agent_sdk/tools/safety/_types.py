# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core types for the Tool Script Safety Guard."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


class Decision(str, Enum):
    """Safety check decision for a tool/script execution."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Severity level of a detected safety risk."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScriptLanguage(str, Enum):
    """Scripting language targeted by the safety scan."""

    PYTHON = "python"
    BASH = "bash"


class ScanTarget(str, Enum):
    """Source context from which the scanned content originates."""

    TOOL = "tool"
    SKILL = "skill"
    MCP_TOOL = "mcp_tool"
    CODE_EXECUTOR = "code_executor"
    FILE_TOOL = "file_tool"


class RiskType(str, Enum):
    """Category of safety risk detected in the scanned content."""

    DANGEROUS_FILE_OPERATION = "dangerous_file_operation"
    NETWORK_EGRESS = "network_egress"
    SYSTEM_COMMAND = "system_command"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SECRET_EXFILTRATION = "secret_exfiltration"


@dataclass
class SafetyFinding:
    """A single safety rule match detected during script scanning."""

    rule_id: str
    rule_name: str
    risk_type: RiskType
    risk_level: RiskLevel
    evidence: str
    recommendation: str
    line: Optional[int] = None
    column: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SafetyReport:
    """Aggregated safety scan report for a single tool/script execution."""

    tool_name: str
    decision: Decision
    risk_level: RiskLevel
    blocked: bool
    sanitized: bool
    duration_ms: int
    language: ScriptLanguage
    target: ScanTarget
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rule_ids: List[str] = field(default_factory=list)
    summary: str = ""
    findings: List[SafetyFinding] = field(default_factory=list)
    telemetry_attributes: Dict[str, Any] = field(default_factory=dict)

    def set_blocked(self, blocked: bool) -> None:
        """Explicitly set the blocked flag after decision aggregation.

        Used by filters and guards to record whether execution was actually
        blocked (which may differ from decision==DENY when block_on_review
        is active).
        """
        self.blocked = blocked


@dataclass
class ScanRequest:
    """Input for a safety scan — script content + execution context."""

    script: str
    language: ScriptLanguage
    tool_name: str
    target: ScanTarget = ScanTarget.TOOL
    args: List[str] = field(default_factory=list)
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    tool_metadata: Dict[str, Any] = field(default_factory=dict)


def normalize_language(language: str) -> ScriptLanguage:
    """Normalize a language string to a ScriptLanguage enum value.

    Mapping:
    - "py", "python", "python3" -> PYTHON
    - "shell", "sh", "bash", "zsh", "" -> BASH
    """
    lang = language.lower().strip()
    if lang in ("py", "python", "python3"):
        return ScriptLanguage.PYTHON
    return ScriptLanguage.BASH


_RISK_ORDER: Dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_DECISION_ORDER: Dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.NEEDS_HUMAN_REVIEW: 1,
    Decision.DENY: 2,
}


def risk_order(level: RiskLevel) -> int:
    """Return numeric severity order of a RiskLevel (0=LOW .. 3=CRITICAL)."""
    return _RISK_ORDER.get(level, 0)


def decision_order(decision: Decision) -> int:
    """Return numeric precedence of a Decision (0=ALLOW .. 2=DENY)."""
    return _DECISION_ORDER.get(decision, 0)


def max_risk_level(findings: List[SafetyFinding]) -> RiskLevel:
    """Return the highest RiskLevel from a list of findings (LOW if empty)."""
    if not findings:
        return RiskLevel.LOW
    return max(findings, key=lambda f: risk_order(f.risk_level)).risk_level


def aggregate_decision(findings: List[SafetyFinding]) -> Decision:
    """Compute the aggregate Decision from a list of findings.

    Rules:
    - Any CRITICAL or HIGH finding -> DENY
    - Any MEDIUM finding -> NEEDS_HUMAN_REVIEW
    - Only LOW or empty -> ALLOW
    """
    if not findings:
        return Decision.ALLOW
    max_level = max_risk_level(findings)
    if max_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
        return Decision.DENY
    if max_level == RiskLevel.MEDIUM:
        return Decision.NEEDS_HUMAN_REVIEW
    return Decision.ALLOW


def _scanner_error_finding() -> SafetyFinding:
    """Return the canonical fail-closed finding for scanner exceptions."""
    return SafetyFinding(
        rule_id="SAFETY_SCANNER_ERROR",
        rule_name="Safety Scanner Error",
        risk_type=RiskType.SYSTEM_COMMAND,
        risk_level=RiskLevel.CRITICAL,
        evidence="Scanner error",
        recommendation="Scanner failed; execution blocked.",
    )
