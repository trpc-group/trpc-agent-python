"""Shared data models for the code review example."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any


SEVERITY_RANK = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}


def utc_now_iso() -> str:
    """Return a compact UTC timestamp string."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ChangedLine:
    """One line in a unified diff hunk."""

    file: str
    old_line: int | None
    new_line: int | None
    kind: str
    content: str


@dataclass
class DiffHunk:
    """A unified diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str = ""
    lines: list[ChangedLine] = field(default_factory=list)


@dataclass
class ChangedFile:
    """A file touched by a diff."""

    old_path: str
    new_path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_deleted: bool = False
    is_new: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path

    @property
    def added_lines(self) -> list[ChangedLine]:
        out: list[ChangedLine] = []
        for hunk in self.hunks:
            out.extend([line for line in hunk.lines if line.kind == "+"])
        return out


@dataclass
class Finding:
    """A structured code review result."""

    severity: str
    category: str
    file: str
    line: int | None
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: str
    disposition: str = "finding"

    def dedupe_key(self) -> tuple[str, int | None, str]:
        return (self.file, self.line, self.category)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 2)
        return data


@dataclass
class FilterDecision:
    """Decision made before a sandbox command is allowed to run."""

    action: str
    rule_id: str
    reason: str
    command: str = ""
    path: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SandboxRequest:
    """A sandbox execution request."""

    name: str
    command: list[str]
    display_command: str
    cwd: str
    input_files: dict[str, str] = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)
    timeout_seconds: float = 10.0
    max_output_bytes: int = 65536
    env: dict[str, str] = field(default_factory=dict)
    allow_network: bool = False


@dataclass
class SandboxRun:
    """Result of one sandbox run or filter-denied request."""

    name: str
    runtime: str
    command: str
    status: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    artifacts: dict[str, str] = field(default_factory=dict)
    error_type: str = ""
    filter_decision: FilterDecision | None = None
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.filter_decision:
            data["filter_decision"] = self.filter_decision.to_dict()
        return data


@dataclass
class ReviewMetrics:
    """Monitoring and audit metrics for a review."""

    total_duration_ms: int = 0
    sandbox_duration_ms: int = 0
    tool_call_count: int = 0
    intercept_count: int = 0
    finding_count: int = 0
    warning_count: int = 0
    needs_human_review_count: int = 0
    severity_distribution: dict[str, int] = field(default_factory=dict)
    exception_type_distribution: dict[str, int] = field(default_factory=dict)
    redaction_count: int = 0
    changed_file_count: int = 0
    changed_line_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
