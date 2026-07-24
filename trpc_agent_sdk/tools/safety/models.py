# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data models for tool script safety checks."""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class SafetyDecision(str, Enum):
    """Final decision from a safety check."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class SafetySeverity(str, Enum):
    """Severity of a safety finding."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolExecutionRequest(BaseModel):
    """Input passed to safety rules before a tool execution."""

    tool_name: str = Field(default="", description="Name of the tool being executed.")
    args: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments.")
    language: str = Field(default="", description="Script language when known.")
    script: str = Field(default="", description="Script source to scan when available.")
    agent_name: str = Field(default="", description="Name of the current agent.")
    invocation_id: str = Field(default="", description="Current invocation id.")
    function_call_id: str = Field(default="", description="Current function call id.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extension metadata.")


class Finding(BaseModel):
    """A single issue reported by a safety rule."""

    rule_id: str = Field(description="Identifier of the rule that produced this finding.")
    message: str = Field(description="Human-readable finding message.")
    severity: SafetySeverity = Field(default=SafetySeverity.MEDIUM, description="Finding severity.")
    target: str = Field(default="", description="Optional target such as an argument path or code block id.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Rule-specific metadata.")


class SafetyResult(BaseModel):
    """Result returned by the checker for one tool execution request."""

    decision: SafetyDecision = Field(default=SafetyDecision.ALLOW, description="Final safety decision.")
    findings: List[Finding] = Field(default_factory=list, description="Findings produced by enabled rules.")
    request: Optional[ToolExecutionRequest] = Field(default=None, description="Request that was checked.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Checker-specific metadata.")

    @property
    def allowed(self) -> bool:
        """Return whether execution should continue."""
        return self.decision == SafetyDecision.ALLOW
