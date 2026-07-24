# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pydantic data models for the code review agent.

These models mirror the DB schema (5 tables) and are used throughout
the review pipeline for type-safe data exchange.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Status of a review task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FindingSeverity(str, Enum):
    """Severity level of a finding."""
    CRITICAL = "critical"
    WARNING = "warning"
    SUGGESTION = "suggestion"


class FindingCategory(str, Enum):
    """Category of a finding."""
    SECURITY = "security"
    ASYNC = "async"
    RESOURCE_LEAK = "resource_leak"
    DB = "db"
    SECRET = "secret"
    TEST = "test"
    MAINTAINABILITY = "maintainability"


class FindingConfidence(str, Enum):
    """Confidence level of a finding."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingSource(str, Enum):
    """Source of a finding."""
    STATIC_CHECK = "static_check"
    PATTERN_MATCH = "pattern_match"
    LLM = "llm"


class SandboxStatus(str, Enum):
    """Status of a sandbox execution."""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILED = "failed"
    INTERCEPTED = "intercepted"


class FilterAction(str, Enum):
    """Action taken by a filter."""
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class FilterType(str, Enum):
    """Type of filter."""
    SANDBOX = "sandbox"
    SECRET = "secret"
    NETWORK = "network"
    BUDGET = "budget"


class ReportType(str, Enum):
    """Type of review report."""
    JSON = "json"
    MARKDOWN = "markdown"


def _new_id() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


def _now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


class ReviewTask(BaseModel):
    """Review task record (maps to review_tasks table)."""
    id: str = Field(default_factory=_new_id)
    input_type: str = "diff_file"  # diff_file | repo_path | fixture
    input_summary: Optional[str] = None  # JSON
    status: TaskStatus = TaskStatus.PENDING
    total_duration_ms: float = 0.0
    finding_count: int = 0
    severity_distribution: Optional[str] = None  # JSON: {"critical": N, ...}
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SandboxRun(BaseModel):
    """Sandbox execution record (maps to sandbox_runs table)."""
    id: str = Field(default_factory=_new_id)
    task_id: str = ""
    script_name: str = ""
    status: SandboxStatus = SandboxStatus.FAILED
    duration_ms: float = 0.0
    output_size_bytes: int = 0
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    intercept_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Finding(BaseModel):
    """Code review finding (maps to findings table)."""
    id: str = Field(default_factory=_new_id)
    task_id: str = ""
    severity: FindingSeverity = FindingSeverity.WARNING
    category: FindingCategory = FindingCategory.SECURITY
    file_path: str = ""
    line_number: int = 0
    title: str = ""
    evidence: Optional[str] = None
    recommendation: Optional[str] = None
    confidence: FindingConfidence = FindingConfidence.MEDIUM
    source: FindingSource = FindingSource.PATTERN_MATCH
    dedup_key: Optional[str] = None
    is_duplicate: bool = False
    needs_human_review: bool = False
    created_at: datetime = Field(default_factory=_now)


class ReviewReport(BaseModel):
    """Review report record (maps to review_reports table)."""
    id: str = Field(default_factory=_new_id)
    task_id: str = ""
    report_type: ReportType = ReportType.JSON
    content: str = ""
    summary: Optional[str] = None
    filter_intercept_summary: Optional[str] = None  # JSON
    monitoring_metrics: Optional[str] = None  # JSON
    sandbox_exec_summary: Optional[str] = None  # JSON
    created_at: datetime = Field(default_factory=_now)


class FilterLog(BaseModel):
    """Filter interception record (maps to filter_logs table)."""
    id: str = Field(default_factory=_new_id)
    task_id: str = ""
    filter_type: FilterType = FilterType.SANDBOX
    action: FilterAction = FilterAction.ALLOW
    target: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class MonitorSummary(BaseModel):
    """Monitoring metrics for a review task (maps to monitor_summary table)."""
    id: str = Field(default_factory=_new_id)
    task_id: str = ""
    total_duration_ms: float = 0.0
    sandbox_duration_ms: float = 0.0
    tool_call_count: int = 0
    intercept_count: int = 0
    finding_count: int = 0
    severity_distribution: Optional[str] = None  # JSON
    exception_types: Optional[str] = None  # JSON list
    filter_intercepts: Optional[str] = None  # JSON list
    created_at: datetime = Field(default_factory=_now)


class ReviewResult(BaseModel):
    """Aggregated result of a full review pipeline run.

    This is the top-level return type of run_review().
    """
    task: ReviewTask = Field(default_factory=ReviewTask)
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[Finding] = Field(default_factory=list)
    needs_human_review: list[Finding] = Field(default_factory=list)
    sandbox_runs: list[SandboxRun] = Field(default_factory=list)
    filter_intercepts: list[FilterLog] = Field(default_factory=list)
    monitor: Optional[MonitorSummary] = None
    report_path_json: Optional[str] = None
    report_path_md: Optional[str] = None