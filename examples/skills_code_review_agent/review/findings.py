# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Finding model plus dedup and confidence-gating helpers."""
from pydantic import BaseModel

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
CONFIDENCE_THRESHOLD = 0.6


class Finding(BaseModel):
    """One structured review finding."""

    severity: str
    category: str
    file: str
    line: int
    title: str
    evidence: str = ""
    recommendation: str = ""
    confidence: float = 1.0
    source: str = "static"

    @property
    def dedup_key(self) -> str:
        return f"{self.file}:{self.line}:{self.category}"


def _merge(winner: Finding, loser: Finding) -> Finding:
    update = {}
    if SEVERITY_ORDER.get(loser.severity, 0) > SEVERITY_ORDER.get(winner.severity, 0):
        update["severity"] = loser.severity
    if loser.confidence > winner.confidence:
        update["confidence"] = loser.confidence
    if loser.source != winner.source:
        update["source"] = "static+llm"
    return winner.model_copy(update=update) if update else winner


def dedupe(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Collapse findings sharing (file, line, category). Returns (kept, dropped)."""
    kept: dict[str, Finding] = {}
    dropped = []
    for f in findings:
        cur = kept.get(f.dedup_key)
        if cur is None:
            kept[f.dedup_key] = f
        else:
            kept[f.dedup_key] = _merge(cur, f)
            dropped.append(f)
    return list(kept.values()), dropped


def gate(findings: list[Finding], threshold: float = CONFIDENCE_THRESHOLD) -> tuple[list[Finding], list[Finding]]:
    """Split into (reported, needs_human_review) by confidence."""
    reported = [f for f in findings if f.confidence >= threshold]
    needs = [f for f in findings if f.confidence < threshold]
    return reported, needs


def severity_distribution(findings: list[Finding]) -> dict[str, int]:
    """Count findings per severity."""
    dist: dict[str, int] = {}
    for f in findings:
        dist[f.severity] = dist.get(f.severity, 0) + 1
    return dist
