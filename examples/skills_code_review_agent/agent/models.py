"""Domain models shared by the review pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class ChangedLine(BaseModel):
    number: int
    content: str


class ReviewHunk(BaseModel):
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[str] = Field(default_factory=list)


class ChangedFile(BaseModel):
    path: str
    old_path: str | None = None
    hunks: list[ReviewHunk] = Field(default_factory=list)
    candidate_lines: list[ChangedLine] = Field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False


class ReviewInput(BaseModel):
    files: list[ChangedFile] = Field(default_factory=list)
    context: str = ""
    candidate_lines: dict[str, list[int]] = Field(default_factory=dict)
    digest: str
    summary: str
    source_type: str
    source_path: str | None = None


class ExecutionRequest(BaseModel):
    task_id: str = ""
    command: list[str]
    cwd: str
    input_paths: list[str] = Field(default_factory=list)
    network_targets: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    memory_limit_mb: int = 0


class ExecutionBudget(BaseModel):
    max_calls: int = 10
    max_total_seconds: float = 120.0
    calls_used: int = 0
    seconds_used: float = 0.0


class FilterDecision(BaseModel):
    decision: Decision
    reason_code: str
    reason: str
    risk_level: str = "low"
    matched_rule: str = ""
    task_id: str = ""
    command_digest: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class Finding(BaseModel):
    severity: str
    category: str
    file: str
    line: int
    title: str
    evidence: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = "rule"
    needs_human_review: bool = False
    dedupe_key: str = ""
    rule_id: str = ""
    rule_version: str = "1"
    validation_status: str = "not_run"


class SandboxRunResult(BaseModel):
    id: str
    runtime: str
    task_type: str = "custom_rule"
    command: list[str]
    status: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_ms: int = 0
    stdout_summary: str = ""
    stderr_summary: str = ""
    decision: FilterDecision


class ReviewRequest(BaseModel):
    review_input: ReviewInput
    runtime: str = "local"
    dry_run: bool = False
    fake_model: bool = True
    task_id: str | None = None


class ReviewReport(BaseModel):
    task_id: str
    status: str
    conclusion: str
    input_summary: str
    findings: list[Finding] = Field(default_factory=list)
    needs_human_review: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sandbox_runs: list[SandboxRunResult] = Field(default_factory=list)
    filter_decisions: list[FilterDecision] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    rule_set_digest: str = ""
    skipped_checks: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ReportPaths(BaseModel):
    json_path: Path
    markdown_path: Path
    filter_events_path: Path
