# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public data models for tool script safety scanning."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ScriptLanguage(str, Enum):
    """Script languages supported by the safety scanner."""

    PYTHON = "python"
    BASH = "bash"


class SafetyDecision(str, Enum):
    """Possible safety decisions."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Risk severity in ascending order."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskCategory(str, Enum):
    """Risk categories emitted by built-in rules."""

    FILE = "file"
    NETWORK = "network"
    PROCESS = "process"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    SECRET = "secret"
    POLICY = "policy"
    PARSER = "parser"


class SafetyScanRequest(BaseModel):
    """Structured input inspected before a tool or program executes."""

    model_config = ConfigDict(extra="forbid")

    content: str
    language: ScriptLanguage
    argv: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float | None = None
    tool_name: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class SafetyFinding(BaseModel):
    """One rule match within a safety report."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: RiskCategory
    risk_level: RiskLevel
    action: SafetyDecision
    message: str
    evidence: str
    line_number: int | None = None
    column: int | None = None
    recommendation: str


class SafetyReport(BaseModel):
    """Complete deterministic result of one scan."""

    model_config = ConfigDict(extra="forbid")

    decision: SafetyDecision
    risk_level: RiskLevel
    findings: list[SafetyFinding] = Field(default_factory=list)
    duration_ms: float
    sanitized: bool = True
    policy_version: str
    input_sha256: str


class SafetyAuditEvent(BaseModel):
    """Sanitized event suitable for logs, JSONL, or monitoring systems."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    rule_id: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    duration_ms: float
    sanitized: bool
    execution_blocked: bool
    policy_version: str
    input_sha256: str

    @classmethod
    def from_report(cls, tool_name: str, report: SafetyReport) -> "SafetyAuditEvent":
        """Build an audit event without copying evidence or source text."""

        rule_ids = list(dict.fromkeys(finding.rule_id for finding in report.findings))
        return cls(
            tool_name=tool_name,
            decision=report.decision,
            risk_level=report.risk_level,
            rule_id=rule_ids[0] if rule_ids else None,
            rule_ids=rule_ids,
            duration_ms=report.duration_ms,
            sanitized=report.sanitized,
            execution_blocked=report.decision != SafetyDecision.ALLOW,
            policy_version=report.policy_version,
            input_sha256=report.input_sha256,
        )


def report_as_dict(report: SafetyReport) -> dict[str, Any]:
    """Return a JSON-compatible report mapping for tool responses."""

    return report.model_dump(mode="json")
