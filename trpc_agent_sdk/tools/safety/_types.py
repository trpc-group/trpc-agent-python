# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Type definitions for the Tool Script Safety Guard."""

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Optional


class RiskType(str, Enum):
    """Risk categories for script safety scanning."""
    DANGEROUS_FILE_OP = "dangerous_file_operation"
    NETWORK_ACCESS = "network_access"
    SYSTEM_COMMAND = "system_command"
    DEPENDENCY_INSTALL = "dependency_install"
    RESOURCE_ABUSE = "resource_abuse"
    SENSITIVE_INFO_LEAK = "sensitive_info_leak"


class Decision(str, Enum):
    """Safety scan outcome decisions."""
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Severity levels for risk findings."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_RISK_LEVEL_ORDER = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}

_DECISION_ORDER = {
    Decision.ALLOW: 1,
    Decision.NEEDS_HUMAN_REVIEW: 2,
    Decision.DENY: 3,
}


@dataclass
class RuleFinding:
    """A single rule match from the safety scan."""
    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    evidence: str
    message: str
    recommendation: str


@dataclass
class ScanReport:
    """Aggregated result of a safety scan."""
    decision: Decision
    risk_level: Optional[RiskLevel] = None
    findings: list[RuleFinding] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    script_snippet: Optional[str] = None
    scan_error: Optional[str] = None


@dataclass
class AuditEvent:
    """Structured audit record for a safety scan."""
    timestamp: str
    tool_name: str
    decision: str
    risk_level: Optional[str]
    rule_ids: list[str]
    scan_duration_ms: float
    sanitized: bool
    intercepted: bool
    script_hash: str
