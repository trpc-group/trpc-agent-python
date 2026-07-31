# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public data models for the tool script safety guard."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any
from typing import Mapping
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


class RiskLevel(str, Enum):
    """Potential harm severity, independent from the policy decision."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SafetyDecision(str, Enum):
    """Decision made by an effective safety policy."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class SafetyCategory(str, Enum):
    """Stable finding categories exposed by the safety API."""

    FILESYSTEM = "filesystem"
    NETWORK = "network"
    PROCESS = "process"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    SECRET = "secret"
    DYNAMIC_EXECUTION = "dynamic_execution"
    NESTED_SCRIPT = "nested_script"
    ANALYSIS = "analysis"


class SafetyScanRequest(BaseModel):
    """A bounded request to statically scan one source without executing it."""

    model_config = ConfigDict(extra="forbid")

    script: str = Field(default="", repr=False, exclude=True, max_length=2_000_000)
    language: str = Field(min_length=1, max_length=32)
    command: Optional[str] = Field(default=None, repr=False, exclude=True, max_length=8_192)
    argv: tuple[str, ...] = Field(default=(), repr=False, exclude=True)
    cwd: Optional[str] = Field(default=None, repr=False, exclude=True, max_length=4_096)
    env: Mapping[str, str] = Field(default_factory=dict, repr=False, exclude=True)
    tool_name: Optional[str] = Field(default=None, max_length=128)
    source_type: str = Field(default="script", min_length=1, max_length=64)
    source_name: Optional[str] = Field(default=None, max_length=256)
    block_index: Optional[int] = Field(default=None, ge=0)
    invocation_id: Optional[str] = Field(default=None, max_length=128)
    session_id: Optional[str] = Field(default=None, max_length=128)
    metadata: Mapping[str, Any] = Field(default_factory=dict, repr=False, exclude=True)

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        """Normalize common language aliases without guessing unknown values."""
        normalized = value.strip().lower()
        aliases = {
            "py": "python",
            "python3": "python",
            "bash": "shell",
            "sh": "shell",
            "zsh": "shell",
            "dash": "shell",
        }
        return aliases.get(normalized, normalized)

    @field_validator("argv")
    @classmethod
    def bound_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 128:
            raise ValueError("argv must contain at most 128 items")
        if any(len(item) > 8_192 for item in value):
            raise ValueError("argv item exceeds 8192 characters")
        return value

    @field_validator("env")
    @classmethod
    def bound_env(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if len(value) > 64:
            raise ValueError("env must contain at most 64 entries")
        copied: dict[str, str] = {}
        for key, item in value.items():
            key_text = str(key)
            item_text = str(item)
            if len(key_text) > 128 or len(item_text) > 4_096:
                raise ValueError("env key or value exceeds the safety request limit")
            copied[key_text] = item_text
        return copied

    @field_validator("metadata")
    @classmethod
    def bound_metadata(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        if len(value) > 32:
            raise ValueError("metadata must contain at most 32 entries")
        copied: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if len(key_text) > 128:
                raise ValueError("metadata key exceeds 128 characters")
            if isinstance(item, str) and len(item) > 2_048:
                raise ValueError("metadata string exceeds 2048 characters")
            copied[key_text] = item
        return copied


class SafetyFinding(BaseModel):
    """One deterministic, redacted safety fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1, max_length=96)
    category: SafetyCategory
    risk_level: RiskLevel
    message: str = Field(max_length=512)
    evidence: str = Field(default="", max_length=512)
    recommendation: str = Field(default="", max_length=512)
    line_number: Optional[int] = Field(default=None, ge=1)
    column_number: Optional[int] = Field(default=None, ge=0)
    block_index: Optional[int] = Field(default=None, ge=0)
    nested_path: tuple[int, ...] = ()
    redacted: bool = True
    hard_deny: bool = False


class SafetyReport(BaseModel):
    """Final static scan decision safe to expose to callers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    decision: SafetyDecision
    risk_level: Optional[RiskLevel] = None
    rule_ids: tuple[str, ...] = ()
    findings: tuple[SafetyFinding, ...] = ()
    decision_reason: str = Field(default="", max_length=512)
    scan_duration_ms: float = Field(default=0.0, ge=0.0)
    redacted: bool = True
    execution_blocked: bool
    tool_name: Optional[str] = Field(default=None, max_length=128)
    source_type: str = Field(default="script", max_length=64)
    language: str = Field(default="", max_length=32)
    script_hash: str = Field(min_length=64, max_length=64)
    policy_version: str = Field(max_length=128)
    policy_hash: str = Field(min_length=64, max_length=64)
    analysis_complete: bool = True
    failure_code: Optional[str] = Field(default=None, max_length=96)
    rules_evaluated: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)


class SafetyObservation(BaseModel):
    """Immutable, bounded event consumed by audit and monitoring sinks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decision: SafetyDecision
    risk_level: Optional[RiskLevel] = None
    blocked: bool
    review_required: bool
    rule_ids: tuple[str, ...] = ()
    categories: tuple[SafetyCategory, ...] = ()
    finding_count: int = Field(default=0, ge=0)
    analysis_complete: bool = True
    failure_code: Optional[str] = None
    source_type: str
    language: str
    tool_name: Optional[str] = None
    script_hash: str
    policy_version: str
    policy_hash: str
    duration_ms: float = Field(ge=0.0)
    redacted: bool = True


class SafetyHealthSignal(BaseModel):
    """A safe operational signal emitted when an observation sink fails."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    component: str = Field(max_length=64)
    failure_code: str = Field(max_length=96)
    message: str = Field(max_length=256)
    redacted: bool = True


def observation_from_report(report: SafetyReport) -> SafetyObservation:
    """Create the sole observation shape without copying evidence or source."""
    categories = tuple(sorted({finding.category for finding in report.findings}, key=lambda item: item.value))
    return SafetyObservation(
        decision=report.decision,
        risk_level=report.risk_level,
        blocked=report.execution_blocked,
        review_required=report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW,
        rule_ids=report.rule_ids,
        categories=categories,
        finding_count=report.finding_count,
        analysis_complete=report.analysis_complete,
        failure_code=report.failure_code,
        source_type=report.source_type,
        language=report.language,
        tool_name=report.tool_name,
        script_hash=report.script_hash,
        policy_version=report.policy_version,
        policy_hash=report.policy_hash,
        duration_ms=report.scan_duration_ms,
    )
