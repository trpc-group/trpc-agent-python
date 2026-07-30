#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Stable data models for pre-execution tool script safety checks."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


class SafetyDecision(str, Enum):
    """A pre-execution decision."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Finding severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskCategory(str, Enum):
    """Risk families required by the public safety contract."""

    FILE_OPERATION = "file_operation"
    NETWORK_ACCESS = "network_access"
    PROCESS_EXECUTION = "process_execution"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SENSITIVE_DATA = "sensitive_data"
    POLICY = "policy"
    SCAN_ERROR = "scan_error"


class ScriptLanguage(str, Enum):
    """Supported executable payload languages."""

    PYTHON = "python"
    BASH = "bash"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScriptPayload(_StrictModel):
    """One executable payload."""

    language: ScriptLanguage
    content: str
    argv: list[str] = Field(default_factory=list)
    stdin: str = ""
    source: str = "tool_argument"


class ToolMetadata(_StrictModel):
    """Non-authoritative metadata describing the invoking tool."""

    name: str
    tool_type: str = "tool"
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class ScriptScanRequest(_StrictModel):
    """Normalized input shared by Tool filters and CodeExecutor wrappers."""

    payloads: list[ScriptPayload] = Field(min_length=1)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    metadata: ToolMetadata
    requested_timeout: float | None = None
    max_output_bytes: int | None = None


class SafetyFinding(_StrictModel):
    """One redacted rule match."""

    category: RiskCategory
    risk_level: RiskLevel
    rule_id: str
    evidence: str
    recommendation: str
    decision: SafetyDecision
    payload_index: int = 0


class SafetyReport(_StrictModel):
    """Aggregated, safe-to-log scan result."""

    decision: SafetyDecision
    risk_level: RiskLevel
    findings: list[SafetyFinding] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    duration_ms: float
    redacted: bool
    summary: str
    policy_version: str
    review_required: bool | None = None

    @model_validator(mode="after")
    def _derive_rule_ids(self) -> "SafetyReport":
        """Derive or validate fields that must agree with the findings."""

        expected_rule_ids = list(dict.fromkeys(finding.rule_id for finding in self.findings))
        if self.rule_ids and self.rule_ids != expected_rule_ids:
            raise ValueError("rule_ids must match findings in stable order")
        self.rule_ids = expected_rule_ids
        expected_review = self.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        if self.review_required is None:
            self.review_required = expected_review
        elif self.review_required != expected_review:
            raise ValueError("review_required must match decision")
        return self

    @property
    def blocked(self) -> bool:
        """Only an allow decision may reach an executor."""

        return self.decision != SafetyDecision.ALLOW


class SafetyAuditEvent(_StrictModel):
    """Minimal monitoring event emitted before execution."""

    timestamp: str
    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    rule_ids: list[str]
    duration_ms: float
    redacted: bool
    execution_blocked: bool
    policy_version: str


class BlockedSafetyResponse(_StrictModel):
    """Structured response returned instead of invoking an unsafe tool."""

    success: bool = False
    error: str
    message: str
    review_required: bool
    safety_report: dict[str, Any]
