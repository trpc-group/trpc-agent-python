# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety scanner data types. Mirrors trpc-agent-go/tool/safety/safety.go."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Decision & RiskLevel
# ---------------------------------------------------------------------------


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


DECISION_ALLOW = Decision.ALLOW
DECISION_DENY = Decision.DENY
DECISION_ASK = Decision.ASK
DECISION_NEEDS_HUMAN_REVIEW = Decision.NEEDS_HUMAN_REVIEW


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_LOW = RiskLevel.LOW
RISK_MEDIUM = RiskLevel.MEDIUM
RISK_HIGH = RiskLevel.HIGH
RISK_CRITICAL = RiskLevel.CRITICAL


def decision_rank(d: Decision) -> int:
    _map = {
        Decision.ALLOW: 1,
        Decision.ASK: 2,
        Decision.NEEDS_HUMAN_REVIEW: 3,
        Decision.DENY: 4,
    }
    return _map.get(d, 0)


def risk_rank(level: RiskLevel) -> int:
    _map = {
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }
    return _map.get(level, 0)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass
class Policy:
    denied_commands: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=list)
    network_allowlist: list[str] = field(default_factory=list)
    env_allowlist: list[str] = field(default_factory=list)
    review_commands: list[str] = field(default_factory=list)
    max_timeout_seconds: int = 0
    max_output_bytes: int = 0
    review_shell_pipelines: bool = True
    deny_on_parse_error: bool = True


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass
class CodeBlock:
    language: str = ""
    code: str = ""


@dataclass
class Request:
    tool_name: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    backend: str = ""
    timeout_seconds: int = 0
    max_output_bytes: int = 0
    background: bool = False
    tty: bool = False
    code_blocks: list[CodeBlock] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Finding & Report
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    decision: Decision = Decision.ALLOW
    risk_level: RiskLevel = RiskLevel.LOW
    rule_id: str = ""
    evidence: list[str] = field(default_factory=list)
    recommendation: str = ""


def finding_beats(a: Finding, b: Finding) -> bool:
    if decision_rank(a.decision) != decision_rank(b.decision):
        return decision_rank(a.decision) > decision_rank(b.decision)
    return risk_rank(a.risk_level) > risk_rank(b.risk_level)


@dataclass
class Report:
    decision: Decision = Decision.ALLOW
    risk_level: RiskLevel = RiskLevel.LOW
    rule_id: str = ""
    evidence: list[str] = field(default_factory=list)
    recommendation: str = ""
    tool_name: str = ""
    command: str = ""
    backend: str = ""
    blocked: bool = False
    redacted: bool = False
    duration_ms: int = 0
    safe_summary: str = ""
    findings: list[Finding] = field(default_factory=list)

    def span_attributes(self) -> dict[str, str]:
        return {
            "tool.safety.decision": self.decision.value,
            "tool.safety.risk_level": self.risk_level.value,
            "tool.safety.rule_id": self.rule_id,
            "tool.safety.backend": self.backend,
        }


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


@dataclass
class AuditEvent:
    timestamp: float = field(default_factory=time.time)
    tool_name: str = ""
    decision: Decision = Decision.ALLOW
    risk_level: RiskLevel = RiskLevel.LOW
    rule_id: str = ""
    duration_ms: int = 0
    redacted: bool = False
    blocked: bool = False
    backend: str = ""
