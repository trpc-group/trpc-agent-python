# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core data models for the Tool Script Safety Guard.

This module defines the vocabulary shared across the safety subsystem:

- :class:`SafetyDecision`: the tri-state verdict (allow / deny /
  needs_human_review) inspired by Claude Code's ``deny > ask > allow`` ordering.
- :class:`RiskLevel` / :class:`RiskCategory`: severity and taxonomy of the six
  risk families required by the issue.
- :class:`RuleHit`: a single matched rule with evidence and recommendation.
- :class:`ScanInput` / :class:`ScanReport`: structured request and report,
  serialisable to ``tool_safety_report.json`` via ``model_dump``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class SafetyDecision(str, Enum):
    """Tri-state verdict produced by the scanner.

    ``needs_human_review`` exists precisely so that uncertain cases are never
    silently allowed, satisfying the "不能把所有不确定情况都直接放行" requirement.
    """

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Severity of a matched rule.

    The ordering (via :meth:`weight`) drives decision fusion: ``critical`` and
    ``high`` force a deny, ``medium`` forces human review, ``low`` is advisory.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def weight(self) -> int:
        """Return a monotonically increasing weight for comparison."""
        return {
            RiskLevel.LOW: 1,
            RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3,
            RiskLevel.CRITICAL: 4,
        }[self]


class RiskCategory(str, Enum):
    """The six risk families enumerated by issue #90."""

    DANGEROUS_FILE_OP = "dangerous_file_operation"
    NETWORK_EXFILTRATION = "network_exfiltration"
    PROCESS_SYSTEM_COMMAND = "process_system_command"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SENSITIVE_INFO_LEAK = "sensitive_info_leak"


class ScriptLanguage(str, Enum):
    """Language of the scanned script."""

    PYTHON = "python"
    BASH = "bash"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, value: Optional[str]) -> "ScriptLanguage":
        """Normalise a free-form language string to a known enum member."""
        if not value:
            return cls.UNKNOWN
        normalized = value.strip().lower()
        if normalized in ("python", "py", "python3"):
            return cls.PYTHON
        if normalized in ("bash", "sh", "shell", "zsh"):
            return cls.BASH
        return cls.UNKNOWN


class RuleHit(BaseModel):
    """A single rule match with the evidence that triggered it."""

    rule_id: str = Field(description="Stable identifier of the matched rule, e.g. 'FS001'.")
    category: RiskCategory = Field(description="Risk family the rule belongs to.")
    risk_level: RiskLevel = Field(description="Severity assigned to the match.")
    title: str = Field(description="Short human-readable rule title.")
    evidence: str = Field(description="The offending snippet extracted from the script.")
    line: Optional[int] = Field(default=None, description="1-based line number of the evidence, if known.")
    recommendation: str = Field(default="", description="Suggested remediation for the match.")
    layer: str = Field(default="regex", description="Detection layer that produced the hit: 'regex' or 'ast'.")


class ScanInput(BaseModel):
    """Everything the guard needs to evaluate one execution request."""

    script: str = Field(description="The script content or command line to scan.")
    language: ScriptLanguage = Field(default=ScriptLanguage.UNKNOWN, description="Script language.")
    tool_name: str = Field(default="<unknown>", description="Name of the tool requesting execution.")
    args: list[str] = Field(default_factory=list, description="Command-line arguments passed to the script.")
    work_dir: Optional[str] = Field(default=None, description="Working directory for the execution.")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables for the execution.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary tool metadata.")


class ScanReport(BaseModel):
    """Structured scan result, directly serialisable to report JSON."""

    tool_name: str = Field(description="Name of the tool that was scanned.")
    language: ScriptLanguage = Field(description="Detected/declared script language.")
    decision: SafetyDecision = Field(description="Final fused verdict.")
    risk_level: RiskLevel = Field(description="Highest risk level across all hits.")
    hits: list[RuleHit] = Field(default_factory=list, description="All rules that matched.")
    summary: str = Field(default="", description="Human-readable summary of the decision.")
    recommendation: str = Field(default="", description="Aggregated recommendation for the caller.")
    redacted: bool = Field(default=False, description="Whether sensitive evidence was masked.")
    duration_ms: float = Field(default=0.0, description="Total scan time in milliseconds.")

    @property
    def blocked(self) -> bool:
        """Whether execution should be prevented (deny or needs_human_review)."""
        return self.decision is not SafetyDecision.ALLOW

    def rule_ids(self) -> list[str]:
        """Return the list of matched rule ids, de-duplicated in order."""
        seen: dict[str, None] = {}
        for hit in self.hits:
            seen.setdefault(hit.rule_id, None)
        return list(seen.keys())
