# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data contracts for tool script safety scanning."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel
from pydantic import Field


class SafetyDecision(str, Enum):
    """Final or per-rule safety decision."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Severity level for a safety finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskType(str, Enum):
    """Risk category used for reporting and aggregation."""

    FILE_OPERATION = "file_operation"
    NETWORK_EGRESS = "network_egress"
    PROCESS_EXECUTION = "process_execution"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SENSITIVE_LEAK = "sensitive_leak"
    POLICY_VIOLATION = "policy_violation"
    PARSER_WARNING = "parser_warning"
    UNKNOWN = "unknown"


class ScriptLanguage(str, Enum):
    """Supported script language hints."""

    PYTHON = "python"
    BASH = "bash"
    SHELL = "shell"
    UNKNOWN = "unknown"


class ScanTarget(BaseModel):
    """Normalized script, command, and metadata to be scanned."""

    content: str = ""
    language: ScriptLanguage = ScriptLanguage.UNKNOWN
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    stdin: str = ""
    timeout_seconds: float | None = None
    output_limit_bytes: int | None = None
    tool_name: str = ""
    tool_metadata: dict[str, Any] = Field(default_factory=dict)


class ScanFinding(BaseModel):
    """Single scanner finding with redacted evidence."""

    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    decision: SafetyDecision
    message: str
    evidence: str
    line: int | None = None
    column: int | None = None
    recommendation: str
    redacted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SafetyReport(BaseModel):
    """Structured scan report returned by the safety guard."""

    decision: SafetyDecision
    risk_level: RiskLevel
    findings: list[ScanFinding] = Field(default_factory=list)
    elapsed_ms: float
    redacted: bool = False
    blocked: bool = False
    language: ScriptLanguage = ScriptLanguage.UNKNOWN
    scanner_version: str = "0.1.0"
    policy_name: str = "default"
    parser_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SafetyAuditEvent(BaseModel):
    """Telemetry-safe summary event for a scan decision."""

    timestamp: str = Field(default_factory=_utc_now_iso)
    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    rule_ids: list[str] = Field(default_factory=list)
    elapsed_ms: float
    redacted: bool
    blocked: bool
    language: ScriptLanguage
    cwd: str = ""
    function_call_id: str = ""
    agent_name: str = ""
    policy_name: str = "default"
    scanner_version: str = "0.1.0"
    finding_count: int = 0
