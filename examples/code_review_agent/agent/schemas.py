# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Schemas for the code review dry-run example."""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


class StrictBaseModel(BaseModel):
    """Base model for public dry-run report schemas."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Severity(str, Enum):
    """Finding severity."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(str, Enum):
    """Finding confidence level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingSource(str, Enum):
    """Where a finding was produced."""

    SKILL = "skill"
    SANDBOX = "sandbox"
    FILTER = "filter"
    FAKE_MODEL = "fake_model"


class ChangedLineKind(str, Enum):
    """Unified diff line kind."""

    ADDED = "added"
    REMOVED = "removed"
    CONTEXT = "context"


class ReviewTaskStatus(str, Enum):
    """Persisted review task status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"


class ChangedLine(StrictBaseModel):
    """A single parsed diff line with old/new line anchors."""

    old_line_number: Optional[int] = Field(default=None, description="Line number in the old file, if present.")
    new_line_number: Optional[int] = Field(default=None, description="Line number in the new file, if present.")
    kind: ChangedLineKind = Field(description="Line kind in the unified diff.")
    text: str = Field(description="Line text without the unified diff prefix.")


class DiffHunk(StrictBaseModel):
    """A unified diff hunk."""

    old_start: int = Field(description="Start line in the old file.")
    old_count: int = Field(description="Line count in the old file hunk.")
    new_start: int = Field(description="Start line in the new file.")
    new_count: int = Field(description="Line count in the new file hunk.")
    section: str = Field(default="", description="Optional hunk section header.")
    changed_lines: list[ChangedLine] = Field(default_factory=list, description="Parsed lines in this hunk.")


class DiffFile(StrictBaseModel):
    """A parsed file entry from a unified diff."""

    old_path: Optional[str] = Field(default=None, description="Old repository-relative path.")
    new_path: Optional[str] = Field(default=None, description="New repository-relative path.")
    status: str = Field(default="modified", description="File status: modified, added, deleted, renamed, or binary.")
    is_binary: bool = Field(default=False, description="Whether this file is represented by a binary diff.")
    hunks: list[DiffHunk] = Field(default_factory=list, description="Parsed hunks for this file.")


class ParsedDiff(StrictBaseModel):
    """A parsed unified diff."""

    files: list[DiffFile] = Field(default_factory=list, description="Parsed file entries.")

    @property
    def hunk_count(self) -> int:
        """Return the total number of hunks."""
        return sum(len(file.hunks) for file in self.files)

    @property
    def changed_line_count(self) -> int:
        """Return the total number of parsed hunk lines."""
        return sum(len(hunk.changed_lines) for file in self.files for hunk in file.hunks)


class ReviewInput(StrictBaseModel):
    """Redacted review input summary."""

    input_type: str = Field(default="diff_file", description="Input mode, such as diff_file or repo_path.")
    repo_path: Optional[str] = Field(default=None, description="Repository path for repo-path reviews.")
    diff_file: Optional[str] = Field(default=None, description="Diff file path for diff-file reviews.")
    base_ref: Optional[str] = Field(default=None, description="Optional base ref used for git diff.")
    changed_files: list[str] = Field(default_factory=list, description="Changed file paths from the parsed diff.")
    diff_sha256: str = Field(default="", description="SHA-256 hash of the redacted diff text.")
    diff_summary: str = Field(default="", description="Short redacted diff summary.")


class SandboxPolicy(StrictBaseModel):
    """Sandbox execution policy."""

    runtime: str = Field(default="fake", description="Sandbox runtime name.")
    timeout_seconds: int = Field(default=10, description="Maximum seconds per sandbox request.")
    max_output_bytes: int = Field(default=4096, description="Maximum stored stdout/stderr bytes.")
    env_allowlist: list[str] = Field(default_factory=list, description="Allowed environment variable names.")
    network_allowed: bool = Field(default=False, description="Whether sandbox network access is allowed.")


class SandboxRun(StrictBaseModel):
    """Recorded sandbox execution result."""

    id: str = Field(description="Sandbox run identifier.")
    script_name: str = Field(description="Allowlisted script name.")
    runtime: str = Field(default="fake", description="Runtime that produced this run.")
    decision: str = Field(default="allow", description="Pre-execution governance decision.")
    exit_code: Optional[int] = Field(default=None, description="Process exit code, if any.")
    timed_out: bool = Field(default=False, description="Whether the run timed out.")
    duration_ms: int = Field(default=0, description="Sandbox run duration in milliseconds.")
    stdout_excerpt: str = Field(default="", description="Redacted stdout excerpt.")
    stderr_excerpt: str = Field(default="", description="Redacted stderr excerpt.")
    output_truncated: bool = Field(default=False, description="Whether output was truncated.")
    error_type: Optional[str] = Field(default=None, description="Failure class, if any.")


class AuditEvent(StrictBaseModel):
    """Review audit event."""

    event_type: str = Field(description="Event type.")
    severity: str = Field(default="info", description="Audit event severity.")
    message: str = Field(description="Redacted event message.")
    details: dict[str, Any] = Field(default_factory=dict, description="Redacted structured details.")


class ReviewFinding(StrictBaseModel):
    """Structured code review finding."""

    severity: Severity = Field(description="Finding severity.")
    category: str = Field(description="Finding category, such as security or secrets.")
    file: str = Field(description="Repository-relative file path.")
    line: int = Field(description="New-file line number from the diff.")
    title: str = Field(description="One-line finding title.")
    evidence: str = Field(description="Concrete code, diff, or sandbox evidence.")
    recommendation: str = Field(description="Actionable fix guidance.")
    confidence: Confidence = Field(description="Finding confidence level.")
    source: FindingSource = Field(description="Finding source.")
    fingerprint: Optional[str] = Field(default=None, description="Stable dedupe key.")
    line_start: Optional[int] = Field(default=None, description="Start line for multi-line findings.")
    line_end: Optional[int] = Field(default=None, description="End line for multi-line findings.")
    needs_human_review: bool = Field(default=False, description="Whether this finding requires human review.")
    raw_source: Optional[str] = Field(default=None, description="Optional raw debug source after redaction.")

    @field_validator("category", "file", "title", "evidence", "recommendation")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class FilterDecision(StrictBaseModel):
    """Decision made by a governance or post-processing filter."""

    filter_name: str = Field(description="Filter that made the decision.")
    decision: str = Field(description="Decision such as allow, deny, merge, redact, or needs_human_review.")
    reason: str = Field(description="Human-readable decision reason.")
    file: Optional[str] = Field(default=None, description="Related file path, if any.")
    line: Optional[int] = Field(default=None, description="Related new-file line number, if any.")
    fingerprint: Optional[str] = Field(default=None, description="Related finding fingerprint, if any.")
    stage: str = Field(default="post", description="Decision stage, such as pre_sandbox or post.")
    script_name: Optional[str] = Field(default=None, description="Related sandbox script, if any.")
    path: Optional[str] = Field(default=None, description="Related path, if any.")
    rule_id: Optional[str] = Field(default=None, description="Related rule identifier, if any.")


class ReviewMetrics(StrictBaseModel):
    """Review report metrics."""

    file_count: int = 0
    hunk_count: int = 0
    changed_line_count: int = 0
    finding_count: int = 0
    warning_count: int = 0
    severity_counts: dict[str, int] = Field(default_factory=dict)
    category_counts: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0
    sandbox_duration_ms: int = 0
    tool_call_count: int = 0
    sandbox_run_count: int = 0
    filter_intercept_count: int = 0
    redaction_count: int = 0
    exception_counts: dict[str, int] = Field(default_factory=dict)


class ReviewReport(StrictBaseModel):
    """Dry-run review report."""

    mode: str = Field(default="dry_run", description="Review mode.")
    summary: str = Field(description="Short report summary.")
    findings: list[ReviewFinding] = Field(default_factory=list)
    warnings: list[ReviewFinding] = Field(default_factory=list)
    filter_decisions: list[FilterDecision] = Field(default_factory=list)
    metrics: ReviewMetrics = Field(default_factory=ReviewMetrics)
    task_id: Optional[str] = Field(default=None, description="Persisted review task id.")
    status: ReviewTaskStatus = Field(default=ReviewTaskStatus.COMPLETED, description="Task status.")
    input: Optional[ReviewInput] = Field(default=None, description="Redacted input summary.")
    sandbox_runs: list[SandboxRun] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)
    final_conclusion: str = Field(default="Review completed.", description="Short final conclusion.")

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dictionary."""
        return self.model_dump(mode="json")
