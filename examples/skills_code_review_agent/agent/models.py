"""Typed contracts for review input, execution, findings, and reports."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class InputKind(str, Enum):
    """Supported review input kinds."""

    DIFF = "diff"
    FILE_LIST = "file_list"
    REPOSITORY = "repository"
    FIXTURE = "fixture"


class LineKind(str, Enum):
    """Unified diff line kinds."""

    CONTEXT = "context"
    ADDED = "added"
    DELETED = "deleted"


class Severity(str, Enum):
    """Finding severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    WARNING = "warning"


class Category(str, Enum):
    """Rule categories required by Issue #92."""

    SECURITY = "security"
    ASYNC_ERROR = "async_error"
    RESOURCE_LEAK = "resource_leak"
    MISSING_TEST = "missing_test"
    SECRET_LEAK = "secret_leak"
    DB_LIFECYCLE = "db_lifecycle"


class DecisionAction(str, Enum):
    """Policy decision actions."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class TaskStatus(str, Enum):
    """Persisted review task states."""

    CREATED = "created"
    FILTERED = "filtered"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    DENIED = "denied"


class SandboxStatus(str, Enum):
    """Sandbox execution states."""

    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"


class ChangedLine(StrictModel):
    """One line in a unified diff hunk."""

    kind: LineKind
    content: str
    old_line: int | None = Field(default=None, ge=1)
    new_line: int | None = Field(default=None, ge=1)


class DiffHunk(StrictModel):
    """A parsed unified diff hunk."""

    header: str
    old_start: int = Field(ge=0)
    old_count: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_count: int = Field(ge=0)
    lines: list[ChangedLine]


class ReviewFile(StrictModel):
    """A file and its changed hunks."""

    old_path: str | None = None
    new_path: str | None = None
    is_binary: bool = False
    hunks: list[DiffHunk] = Field(default_factory=list)


class ReviewInput(StrictModel):
    """Canonical input passed to the review Skill."""

    kind: InputKind
    source: str
    digest: str
    files: list[ReviewFile]
    warnings: list[str] = Field(default_factory=list)


class Finding(StrictModel):
    """The exact nine-field finding contract required by Issue #92."""

    severity: Severity
    category: Category
    file: str
    line: int | None
    title: str
    evidence: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str

    @field_validator("file")
    @classmethod
    def validate_relative_file(cls, value: str) -> str:
        """Reject absolute and parent-traversing finding paths."""
        normalized = value.replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(value)
        if (not normalized or "\x00" in normalized or posix_path.is_absolute() or windows_path.is_absolute()
                or windows_path.drive or ".." in posix_path.parts):
            raise ValueError("finding file must be repository-relative")
        return normalized


class ExecutionPlan(StrictModel):
    """Immutable sandbox request approved by policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...]
    cwd: str
    input_path: str
    output_path: str
    environment: tuple[tuple[str, str], ...] = ()
    runtime: str
    network_allowed: bool = False
    timeout_seconds: float = Field(gt=0)
    output_limit_bytes: int = Field(gt=0)
    input_digest: str
    skill_digest: str
    digest: str


class FilterDecision(StrictModel):
    """One auditable policy decision."""

    action: DecisionAction
    rule_id: str
    reason: str
    plan_digest: str
    created_at: datetime


class SandboxRun(StrictModel):
    """Normalized result from a sandbox invocation."""

    status: SandboxStatus
    exit_code: int | None = None
    timed_out: bool = False
    duration_ms: int = Field(ge=0)
    stdout: str = ""
    stderr: str = ""
    error_type: str | None = None


class ReviewMetrics(StrictModel):
    """Low-cardinality metrics included in a report."""

    total_duration_ms: int = Field(ge=0)
    sandbox_duration_ms: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    filter_actions: dict[str, int] = Field(default_factory=dict)
    findings_by_severity: dict[str, int] = Field(default_factory=dict)
    findings_by_category: dict[str, int] = Field(default_factory=dict)
    exceptions_by_type: dict[str, int] = Field(default_factory=dict)


class ReviewReport(StrictModel):
    """Complete queryable review report."""

    task_id: str
    status: TaskStatus
    input_summary: dict[str, Any]
    findings: list[Finding]
    warnings: list[Finding]
    needs_human_review: list[Finding]
    filter_decisions: list[FilterDecision]
    sandbox_runs: list[SandboxRun]
    failures: list[str]
    metrics: ReviewMetrics
    conclusion: str
    created_at: datetime
