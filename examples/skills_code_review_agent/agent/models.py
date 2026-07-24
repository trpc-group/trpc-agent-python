"""Data contracts for the skills code review agent example."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ChangedLine:
    file: str
    old_line: int | None
    new_line: int | None
    content: str
    kind: str
    hunk_header: str = ""
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Hunk:
    file: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[ChangedLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DiffInput:
    source: str
    diff_text: str
    files: list[str]
    hunks: list[Hunk]
    added_lines: list[ChangedLine]
    summary: dict[str, Any]
    file_changes: list[dict[str, Any]] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "files": self.files,
            "summary": self.summary,
            "file_changes": self.file_changes,
            "parse_warnings": self.parse_warnings,
            "hunks": [h.to_dict() for h in self.hunks],
            "added_lines": [line.to_dict() for line in self.added_lines],
        }


@dataclass(slots=True)
class Finding:
    finding_id: str
    schema_version: int
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
    hunk_header: str = ""
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, int, str]:
        return (self.file, self.line, self.category)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FilterDecision:
    decision: str
    reason: str
    command: str = ""
    path: str = ""
    policy: str = ""
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SandboxRun:
    name: str
    runtime: str
    command: str
    status: str
    exit_code: int | None
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    output_truncated: bool = False
    exception_type: str | None = None
    redaction_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MonitoringSummary:
    total_duration_ms: int = 0
    sandbox_duration_ms: int = 0
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    risk_level: str = "none"
    tool_call_count: int = 0
    filter_decision_count: int = 0
    interception_count: int = 0
    filter_decision_distribution: dict[str, int] = field(default_factory=dict)
    finding_count: int = 0
    warning_count: int = 0
    needs_human_review_count: int = 0
    severity_distribution: dict[str, int] = field(default_factory=dict)
    category_distribution: dict[str, int] = field(default_factory=dict)
    exception_distribution: dict[str, int] = field(default_factory=dict)
    redaction_count: int = 0
    deduped_finding_count: int = 0
    ignored_finding_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReviewReport:
    task_id: str
    status: str
    created_at: str
    finding_schema_version: int
    confidence_thresholds: dict[str, float]
    sandbox_policy: dict[str, Any]
    filter_policy: dict[str, Any]
    input: dict[str, Any]
    findings: list[Finding]
    warnings: list[Finding]
    needs_human_review: list[Finding]
    filter_decisions: list[FilterDecision]
    sandbox_runs: list[SandboxRun]
    monitoring: MonitoringSummary
    conclusion: str
    skill_audit: dict[str, Any] = field(default_factory=dict)
    output_files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "created_at": self.created_at,
            "finding_schema_version": self.finding_schema_version,
            "confidence_thresholds": self.confidence_thresholds,
            "sandbox_policy": self.sandbox_policy,
            "filter_policy": self.filter_policy,
            "input": self.input,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": [f.to_dict() for f in self.warnings],
            "needs_human_review": [f.to_dict() for f in self.needs_human_review],
            "filter_decisions": [d.to_dict() for d in self.filter_decisions],
            "sandbox_runs": [r.to_dict() for r in self.sandbox_runs],
            "monitoring": self.monitoring.to_dict(),
            "conclusion": self.conclusion,
            "skill_audit": self.skill_audit,
            "output_files": self.output_files,
        }
