# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Types for tool script safety scanning."""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class SafetyDecision(str, Enum):
    """Final or per-rule safety decision."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Finding severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskType(str, Enum):
    """Safety risk category."""

    FILE_OPERATION = "file_operation"
    NETWORK_EGRESS = "network_egress"
    PROCESS_EXECUTION = "process_execution"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SENSITIVE_LEAK = "sensitive_leak"
    POLICY = "policy"
    UNKNOWN = "unknown"


class ScriptLanguage(str, Enum):
    """Supported script language hints."""

    PYTHON = "python"
    BASH = "bash"
    UNKNOWN = "unknown"


class RuleOverride(BaseModel):
    """Policy override for a built-in rule."""

    enabled: bool = True
    decision: Optional[SafetyDecision] = None
    risk_level: Optional[RiskLevel] = None


class SafetyPolicy(BaseModel):
    """Configurable safety policy."""

    mode: str = "standard"
    fail_closed: bool = False
    block_on_review: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=lambda: ["~/.ssh", ".env", "/etc", "/var/secrets"])
    max_timeout_seconds: int = 300
    max_output_bytes: int = 10_000
    audit_log_path: Optional[str] = "tool_safety_audit.jsonl"
    rules: dict[str, RuleOverride] = Field(default_factory=dict)


class ScanFinding(BaseModel):
    """A single safety rule hit."""

    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    decision: SafetyDecision
    message: str
    evidence: str
    recommendation: str
    line: Optional[int] = None
    column: Optional[int] = None


class SafetyReport(BaseModel):
    """Structured safety scan result."""

    decision: SafetyDecision
    risk_level: RiskLevel = RiskLevel.LOW
    findings: list[ScanFinding] = Field(default_factory=list)
    elapsed_ms: float = 0
    redacted: bool = False
    blocked: bool = False
    language: ScriptLanguage = ScriptLanguage.UNKNOWN
    tool_name: str = ""
    scanner_version: str = "1"
    error: Optional[str] = None

    @property
    def rule_ids(self) -> list[str]:
        """Return matched rule ids in report order."""
        return [finding.rule_id for finding in self.findings]


class SafetyAuditEvent(BaseModel):
    """JSONL audit event emitted by the safety guard."""

    timestamp: str
    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    rule_ids: list[str] = Field(default_factory=list)
    elapsed_ms: float = 0
    redacted: bool = False
    blocked: bool = False
    language: ScriptLanguage = ScriptLanguage.UNKNOWN
    cwd: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

