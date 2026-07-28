"""Stable output model shared by every detector."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Finding:
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
    rule_version: str = "1"
    validation_status: str = "not_run"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
