# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Finding model, dedup and noise control."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(str, Enum):
    SECURITY_RISK = "security_risk"
    ASYNC_ERROR = "async_error"
    RESOURCE_LEAK = "resource_leak"
    MISSING_TESTS = "missing_tests"
    SECRET_LEAKAGE = "secret_leakage"
    DB_LIFECYCLE = "db_lifecycle"


class Source(str, Enum):
    STATIC_RULE = "static_rule"
    SANDBOX_CHECK = "sandbox_check"
    LLM = "llm"


_SEVERITY_RANK = {
    Severity.CRITICAL.value: 4,
    Severity.HIGH.value: 3,
    Severity.MEDIUM.value: 2,
    Severity.LOW.value: 1,
    Severity.INFO.value: 0,
}

BUCKET_FINDING = "finding"
BUCKET_NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass
class Finding:
    """One structured review finding (fields required by issue requirement 4)."""

    severity: str
    category: str
    file: str
    line: int
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: str
    rule_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> Tuple[str, int, str]:
        return (self.file, self.line, self.category)

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK.get(self.severity, 0)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if not data["extra"]:
            data.pop("extra")
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        return cls(
            severity=str(data.get("severity", Severity.INFO.value)),
            category=str(data.get("category", "")),
            file=str(data.get("file", "")),
            line=int(data.get("line", 0) or 0),
            title=str(data.get("title", "")),
            evidence=str(data.get("evidence", "")),
            recommendation=str(data.get("recommendation", "")),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            source=str(data.get("source", Source.STATIC_RULE.value)),
            rule_id=str(data.get("rule_id", "")),
            extra=dict(data.get("extra") or {}),
        )


def dedup_findings(findings: List[Finding]) -> Tuple[List[Finding], int]:
    """Collapse duplicates: same (file, line, category) reported at most once.

    Keeps the representative with the highest (severity, confidence); merges
    the losing rule ids into ``extra['also_matched']`` so no signal is lost.

    Returns:
        (kept, removed_count)
    """
    best: Dict[Tuple[str, int, str], Finding] = {}
    order: List[Tuple[str, int, str]] = []
    removed = 0
    for finding in findings:
        key = finding.dedup_key
        current = best.get(key)
        if current is None:
            best[key] = finding
            order.append(key)
            continue
        removed += 1
        finding_wins = ((finding.severity_rank, finding.confidence)
                        > (current.severity_rank, current.confidence))
        winner, loser = (finding, current) if finding_wins else (current, finding)
        if loser.rule_id and loser.rule_id != winner.rule_id:
            also = winner.extra.setdefault("also_matched", [])
            if loser.rule_id not in also:
                also.append(loser.rule_id)
        best[key] = winner
    return [best[key] for key in order], removed


def split_noise(findings: List[Finding], min_confidence: float) -> Tuple[List[Finding], List[Finding]]:
    """Split into (high-confidence findings, needs_human_review).

    Low-confidence results NEVER mix into the findings list — they surface in
    the report's 人工复核 section instead (issue requirement 6).
    """
    high: List[Finding] = []
    review: List[Finding] = []
    for finding in findings:
        (high if finding.confidence >= min_confidence else review).append(finding)
    return high, review


def severity_distribution(findings: List[Finding]) -> Dict[str, int]:
    """Count findings per severity, all severities always present."""
    dist = {severity.value: 0 for severity in Severity}
    for finding in findings:
        dist[finding.severity] = dist.get(finding.severity, 0) + 1
    return dist
