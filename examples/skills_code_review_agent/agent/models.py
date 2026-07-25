"""Structured data returned by the code-review-agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    file: str
    line: int | None
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: str = "rule"
    needs_human_review: bool = False
    fingerprint: str = ""

    def as_dict(self) -> dict:
        return asdict(self)
