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
import math
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

# Accommodate large provider/CI environments while bounding aggregate input.
_MAX_ENV_ENTRIES = 1024
_MAX_ENV_TOTAL_BYTES = 1_048_576


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


class AnalysisStatus(str, Enum):
    """Completeness state produced by one language analyzer."""

    COMPLETE = "complete"
    PARSE_ERROR = "parse_error"
    UNSUPPORTED = "unsupported"
    BUDGET_EXCEEDED = "budget_exceeded"
    INTERNAL_ERROR = "internal_error"


class SafetyScanRequest(BaseModel):
    """Structured input inspected before a tool or program executes."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    content: str
    language: ScriptLanguage
    argv: list[str] = Field(default_factory=list, max_length=256)
    cwd: str | None = Field(default=None, max_length=4096)
    env: dict[str, str] = Field(
        default_factory=dict,
        max_length=_MAX_ENV_ENTRIES,
    )
    timeout_seconds: float | None = Field(default=None, gt=0)
    tool_name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, values: list[str]) -> list[str]:
        """Bound individual arguments before analyzers inspect them."""

        if any(len(value) > 4096 for value in values):
            raise ValueError("argv entries cannot exceed 4096 characters")
        return values

    @field_validator("env")
    @classmethod
    def validate_env(cls, values: dict[str, str]) -> dict[str, str]:
        """Bound environment input without copying values into diagnostics."""

        if any(not key or len(key) > 256 or len(value) > 8192 for key, value in values.items()):
            raise ValueError("env keys or values exceed safety input limits")
        total_bytes = sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in values.items())
        if total_bytes > _MAX_ENV_TOTAL_BYTES:
            raise ValueError("env exceeds the total safety input limit")
        return values

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, values: dict[str, str]) -> dict[str, str]:
        """Keep adapter metadata small and non-ambiguous."""

        if any(not key or len(key) > 128 or len(value) > 1024 for key, value in values.items()):
            raise ValueError("metadata keys or values exceed safety input limits")
        return values

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        """Reject non-finite values independently of Pydantic coercion."""

        if value is not None and not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value


class SafetyFinding(BaseModel):
    """One rule match within a safety report."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: RiskCategory
    risk_level: RiskLevel
    action: SafetyDecision
    message: str
    evidence: str
    block_id: str = "block-0"
    line_number: int | None = None
    column: int | None = None
    recommendation: str


class SafetyReport(BaseModel):
    """Complete deterministic result of one scan."""

    model_config = ConfigDict(extra="forbid")

    decision: SafetyDecision
    risk_level: RiskLevel
    rule_id: str
    evidence: str
    recommendation: str
    findings: list[SafetyFinding] = Field(default_factory=list)
    duration_ms: float
    sanitized: bool = True
    policy_version: str
    policy_api_version: str = "trpc-agent.io/tool-safety/v1"
    policy_id: str = "default"
    policy_relaxed: bool = False
    input_sha256: str
    analysis_complete: bool = True
    analysis_status: AnalysisStatus = AnalysisStatus.COMPLETE
    invocation_id: str
    blocks_scanned: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_analysis_invariants(self) -> "SafetyReport":
        """Reject reports that could automatically allow incomplete analysis."""

        complete = self.analysis_status == AnalysisStatus.COMPLETE
        if self.analysis_complete != complete:
            raise ValueError("analysis_complete must match analysis_status")
        if not complete and self.decision == SafetyDecision.ALLOW:
            raise ValueError("incomplete analysis cannot have an allow decision")
        if not self.findings:
            if self.decision != SafetyDecision.ALLOW:
                raise ValueError("a report without findings must be allow")
            if self.rule_id != "ALLOW-000" or self.risk_level != RiskLevel.NONE:
                raise ValueError("a report without findings must use ALLOW-000 and risk none")
        else:
            if any(finding.action == SafetyDecision.DENY for finding in self.findings):
                expected = SafetyDecision.DENY
            elif any(finding.action == SafetyDecision.NEEDS_HUMAN_REVIEW for finding in self.findings):
                expected = SafetyDecision.NEEDS_HUMAN_REVIEW
            else:
                expected = SafetyDecision.ALLOW
            if self.decision != expected:
                raise ValueError("decision must match the strictest finding action")
            risk_order = {
                RiskLevel.NONE: 0,
                RiskLevel.LOW: 1,
                RiskLevel.MEDIUM: 2,
                RiskLevel.HIGH: 3,
                RiskLevel.CRITICAL: 4,
            }
            expected_risk = max(
                (finding.risk_level for finding in self.findings),
                key=risk_order.__getitem__,
            )
            if self.risk_level != expected_risk:
                raise ValueError("risk_level must match the highest finding risk")
            primary = self.findings[0]
            if (self.rule_id != primary.rule_id or self.evidence != primary.evidence
                    or self.recommendation != primary.recommendation):
                raise ValueError("top-level report fields must match the primary finding")
        return self


class SafetyAuditEvent(BaseModel):
    """Sanitized event suitable for logs, JSONL, or monitoring systems."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    rule_id: str
    rule_ids: list[str] = Field(default_factory=list)
    duration_ms: float
    sanitized: bool
    execution_blocked: bool
    invocation_id: str
    policy_version: str
    policy_api_version: str
    policy_id: str
    policy_relaxed: bool
    input_sha256: str
    blocks_scanned: int
    analysis_complete: bool
    analysis_status: AnalysisStatus
    adapter_kind: str | None = None

    @classmethod
    def from_report(
        cls,
        tool_name: str,
        report: SafetyReport,
        *,
        adapter_kind: str | None = None,
    ) -> "SafetyAuditEvent":
        """Build an audit event without copying evidence or source text."""

        rule_ids = list(dict.fromkeys(finding.rule_id for finding in report.findings))
        return cls(
            tool_name=tool_name,
            decision=report.decision,
            risk_level=report.risk_level,
            rule_id=report.rule_id,
            rule_ids=rule_ids,
            duration_ms=report.duration_ms,
            sanitized=report.sanitized,
            execution_blocked=report.decision != SafetyDecision.ALLOW,
            invocation_id=report.invocation_id,
            policy_version=report.policy_version,
            policy_api_version=report.policy_api_version,
            policy_id=report.policy_id,
            policy_relaxed=report.policy_relaxed,
            input_sha256=report.input_sha256,
            blocks_scanned=report.blocks_scanned,
            analysis_complete=report.analysis_complete,
            analysis_status=report.analysis_status,
            adapter_kind=adapter_kind,
        )
