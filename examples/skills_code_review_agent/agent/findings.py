"""Finding data model for deterministic code review."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """A structured code review finding."""

    severity: str
    category: str
    file: str
    line: int
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
