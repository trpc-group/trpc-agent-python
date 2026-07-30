"""Domain models for the code review example."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ChangeType(str, Enum):
    """Git file change type."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"
    UNMERGED = "unmerged"
    UNKNOWN = "unknown"


class LineChangeType(str, Enum):
    """Unified diff line type."""

    CONTEXT = "context"
    ADDED = "added"
    DELETED = "deleted"


class Severity(str, Enum):
    """Finding severity from most to least urgent."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReviewStatus(str, Enum):
    """Lifecycle of a local review run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalyzerStatus(str, Enum):
    """Outcome of one deterministic analyzer invocation."""

    SUCCESS = "success"
    FINDINGS = "findings"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"


class ChangedLine(BaseModel):
    """One line in a unified diff hunk."""

    change_type: LineChangeType
    content: str
    old_line: int | None = None
    new_line: int | None = None


class DiffHunk(BaseModel):
    """A parsed unified diff hunk."""

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str = ""
    lines: list[ChangedLine] = Field(default_factory=list)


class ChangedFile(BaseModel):
    """A changed file and its parsed patch."""

    path: str
    old_path: str | None = None
    change_type: ChangeType = ChangeType.UNKNOWN
    language: str = "text"
    patch: str = ""
    hunks: list[DiffHunk] = Field(default_factory=list)
    added_lines: int = 0
    deleted_lines: int = 0
    is_binary: bool = False
    is_truncated: bool = False

    @property
    def changed_new_lines(self) -> set[int]:
        """Return new-file line numbers introduced by the patch."""
        return {
            line.new_line
            for hunk in self.hunks
            for line in hunk.lines if line.change_type == LineChangeType.ADDED and line.new_line is not None
        }


class Finding(BaseModel):
    """One normalized, machine-consumable code review finding."""

    rule_id: str = Field(min_length=1, max_length=160)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    category: str = Field(min_length=1, max_length=80)
    file_path: str = Field(min_length=1)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    suggestion: str = ""
    source: str = Field(default="llm", min_length=1, max_length=80)
    publishable: bool = False

    @field_validator("rule_id", "category", "file_path", "title", "description", "suggestion", "source")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """Remove accidental leading/trailing whitespace."""
        return value.strip()

    @field_validator("rule_id", "category", "source")
    @classmethod
    def normalize_labels(cls, value: str) -> str:
        """Normalize identifiers used for filtering and deduplication."""
        return value.casefold()

    @model_validator(mode="after")
    def validate_line_range(self) -> Finding:
        """Keep line ranges internally consistent."""
        if self.end_line is None and self.start_line is not None:
            self.end_line = self.start_line
        if self.start_line is None and self.end_line is not None:
            raise ValueError("end_line requires start_line")
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class ReviewOutput(BaseModel):
    """Structured final response produced by the review agent."""

    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)


class AnalyzerExecution(BaseModel):
    """Auditable result of one static-analysis command."""

    tool: str
    runtime: str
    command: list[str] = Field(default_factory=list)
    status: AnalyzerStatus
    exit_code: int | None = None
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    findings_count: int = 0


class ReviewRun(BaseModel):
    """Metadata and final result for one local review execution."""

    id: str
    repository_path: str
    base_revision: str
    head_revision: str
    resolved_head_revision: str = ""
    effective_base_revision: str
    status: ReviewStatus = ReviewStatus.PENDING
    model_name: str = ""
    config_hash: str
    idempotency_key: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    error_message: str = ""
    changed_files: list[ChangedFile] = Field(default_factory=list)
    static_analysis_requested: bool = False
    analyzer_executions: list[AnalyzerExecution] = Field(default_factory=list)
    output: ReviewOutput = Field(default_factory=ReviewOutput)
    diagnostics: list[str] = Field(default_factory=list)


def stable_config_hash(config: dict[str, Any]) -> str:
    """Return a stable digest for idempotency and report traceability."""
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()
