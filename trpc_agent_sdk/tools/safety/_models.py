# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data models for tool script safety scanning."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel
from pydantic import Field


class SafetyDecision(str, Enum):
    """Execution decision produced by the scanner."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Ordered risk level used by findings and reports."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SafetyFinding(BaseModel):
    """One rule match with actionable evidence."""

    risk_type: str
    risk_level: RiskLevel
    rule_id: str
    evidence: str
    recommendation: str
    line: int | None = None


class ToolSafetyRequest(BaseModel):
    """Inputs available before a script-capable tool executes."""

    tool_name: str
    script: str = ""
    language: str = "auto"
    command_args: list[str] = Field(default_factory=list)
    working_directory: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolSafetyReport(BaseModel):
    """Structured, monitoring-friendly scan result."""

    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    findings: list[SafetyFinding]
    rule_ids: list[str]
    duration_ms: float
    redacted: bool
    blocked: bool
    telemetry_attributes: dict[str, str]
