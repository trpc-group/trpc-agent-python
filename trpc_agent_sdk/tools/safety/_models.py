# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data contracts for tool script safety checks."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class SafetyDecision(str, Enum):
    """Final action selected by the safety guard."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Normalized risk level for findings and reports."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskCategory(str, Enum):
    """Risk families required by the tool safety policy."""

    DANGEROUS_FILE_OPERATION = "dangerous_file_operation"
    NETWORK_ACCESS = "network_access"
    PROCESS_EXECUTION = "process_execution"
    DEPENDENCY_INSTALLATION = "dependency_installation"
    RESOURCE_ABUSE = "resource_abuse"
    SENSITIVE_DATA_EXPOSURE = "sensitive_data_exposure"
    POLICY_VIOLATION = "policy_violation"
    SCAN_ERROR = "scan_error"


class ScriptLanguage(str, Enum):
    """Script languages supported by the static scanner."""

    PYTHON = "python"
    BASH = "bash"


class SafetyFinding(BaseModel):
    """One rule match produced by a static safety scan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1)
    category: RiskCategory
    risk_level: RiskLevel
    decision: SafetyDecision
    evidence: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    line_number: Optional[int] = Field(default=None, ge=1)
    column: Optional[int] = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SafetyScanRequest(BaseModel):
    """Sanitized execution input accepted by :class:`ToolSafetyScanner`.

    Environment values are intentionally excluded. Their names are sufficient
    for static taint checks and retaining values would create a second secret
    store in reports, audit events, or validation errors.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str
    language: ScriptLanguage
    tool_name: str = "unknown_tool"
    argv: list[str] = Field(default_factory=list)
    cwd: Optional[str] = None
    environment_keys: list[str] = Field(default_factory=list)
    timeout_seconds: Optional[float] = Field(default=None, ge=0)
    output_limit_bytes: Optional[int] = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_execution(
        cls,
        *,
        script: str,
        language: ScriptLanguage | str,
        tool_name: str = "unknown_tool",
        argv: Optional[list[str]] = None,
        cwd: Optional[str] = None,
        environment: Optional[dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        output_limit_bytes: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "SafetyScanRequest":
        """Build a request without retaining environment values."""

        return cls(
            script=script,
            language=language,
            tool_name=tool_name,
            argv=list(argv or []),
            cwd=cwd,
            environment_keys=sorted(str(key) for key in (environment or {})),
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
            metadata=dict(metadata or {}),
        )


class SafetyReport(BaseModel):
    """Structured safety decision returned before execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    language: ScriptLanguage
    languages: list[ScriptLanguage] = Field(default_factory=list)
    decision: SafetyDecision
    risk_level: RiskLevel
    findings: list[SafetyFinding] = Field(default_factory=list)
    rule_id: Optional[str] = None
    rule_ids: list[str] = Field(default_factory=list)
    duration_ms: float = Field(ge=0)
    script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str
    redacted: bool = True
    blocked: bool
    human_review_approved: bool = False
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SafetyAuditEvent(BaseModel):
    """Compact event intended for JSONL logs and monitoring pipelines."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    rule_id: Optional[str] = None
    rule_ids: list[str] = Field(default_factory=list)
    duration_ms: float = Field(ge=0)
    redacted: bool
    blocked: bool
    human_review_approved: bool = False
    script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str


RISK_LEVEL_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

DECISION_ORDER: dict[SafetyDecision, int] = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}


def highest_risk_level(findings: list[SafetyFinding]) -> RiskLevel:
    """Return the highest normalized risk, or ``low`` for a clean scan."""

    if not findings:
        return RiskLevel.LOW
    return max((finding.risk_level for finding in findings), key=RISK_LEVEL_ORDER.__getitem__)


def strictest_decision(findings: list[SafetyFinding]) -> SafetyDecision:
    """Return the strictest finding action, or ``allow`` for a clean scan."""

    if not findings:
        return SafetyDecision.ALLOW
    return max((finding.decision for finding in findings), key=DECISION_ORDER.__getitem__)
