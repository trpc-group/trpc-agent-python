"""Structured models used across review, persistence, and reporting."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass(frozen=True)
class ChangedLine:
    file: str
    line: int
    text: str
    hunk: str


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
    rule_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SandboxResult:
    command: list[str]
    status: str
    exit_code: int | None
    duration_ms: float
    output: str
    error_type: str | None = None
    filter_decision: str = "allow"
    filter_reason: str | None = None
    redacted: bool = False


@dataclass
class ReviewReport:
    task_id: str
    status: str
    conclusion: str
    input_summary: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    filter_blocks: list[dict[str, Any]] = field(default_factory=list)
    sandbox_runs: list[SandboxResult] = field(default_factory=list)
    monitoring: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "conclusion": self.conclusion,
            "input_summary": self.input_summary,
            "findings": [item.to_dict() for item in self.findings],
            "warnings": [item.to_dict() for item in self.warnings],
            "filter_blocks": self.filter_blocks,
            "sandbox_runs": [asdict(item) for item in self.sandbox_runs],
            "monitoring": self.monitoring,
        }
