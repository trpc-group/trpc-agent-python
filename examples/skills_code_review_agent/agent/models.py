# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQLite-friendly data contracts for the skills code review agent example."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class InputType(str, Enum):
    """Supported review input modes."""

    DIFF_FILE = "diff_file"
    REPO_PATH = "repo_path"
    FIXTURE = "fixture"
    FILE_LIST = "file_list"


class TaskStatus(str, Enum):
    """Lifecycle states for a review task."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class RuntimeKind(str, Enum):
    """Runtime choices for rule and sandbox execution."""

    DRY_RUN = "dry-run"
    CONTAINER = "container"
    CUBE = "cube"
    LOCAL_DEV = "local-dev"


class SandboxStatus(str, Enum):
    """Outcome states for a sandbox command."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILED = "failed"
    DENIED = "denied"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class FindingSeverity(str, Enum):
    """Finding severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(str, Enum):
    """Initial review categories covered by the example."""

    SECURITY = "security"
    ASYNC = "async"
    RESOURCE = "resource"
    TEST = "test"
    SECRET = "secret"
    DB = "db"
    GOVERNANCE = "governance"
    SANDBOX = "sandbox"


class FindingSource(str, Enum):
    """Origin of a finding."""

    RULE = "rule"
    FAKE_MODEL = "fake_model"
    SANDBOX = "sandbox"
    STATIC_CHECK = "static_check"
    FILTER = "filter"


class FilterDecision(str, Enum):
    """Governance decisions before potentially risky execution."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class FilterReasonCode(str, Enum):
    """Machine-readable reason for a governance decision."""

    HIGH_RISK_COMMAND = "high_risk_command"
    FORBIDDEN_PATH = "forbidden_path"
    NETWORK_DENIED = "network_denied"
    BUDGET_EXCEEDED = "budget_exceeded"
    LOCAL_RUNTIME_DENIED = "local_runtime_denied"
    ENV_NOT_ALLOWED = "env_not_allowed"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    UNKNOWN = "unknown"


class FilterTargetType(str, Enum):
    """The reviewed object type for a governance decision."""

    COMMAND = "command"
    SCRIPT = "script"
    PATH = "path"
    NETWORK = "network"
    RUNTIME = "runtime"
    BUDGET = "budget"
    ENV = "env"
    TOOL = "tool"


def new_id(prefix: str) -> str:
    """Create a compact stable identifier with a human-readable prefix."""
    clean_prefix = prefix.strip().replace("_", "-")
    return f"{clean_prefix}-{uuid4().hex}"


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value


class DictMixin:
    """Mixin for future JSON, SQLite, and Markdown report rendering."""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return _to_jsonable(asdict(self))


@dataclass
class ReviewTask(DictMixin):
    """A single code review task."""

    input_type: InputType
    input_ref: str
    id: str = field(default_factory=lambda: new_id("review-task"))
    status: TaskStatus = TaskStatus.PENDING
    summary: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None


@dataclass
class ChangedLine(DictMixin):
    """A changed line inside a unified diff hunk."""

    line_type: str
    content: str
    old_line: int | None = None
    new_line: int | None = None


@dataclass
class DiffHunk(DictMixin):
    """A unified diff hunk with source and target ranges."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section_header: str = ""
    lines: list[ChangedLine] = field(default_factory=list)


@dataclass
class ChangedFile(DictMixin):
    """A changed file and its parsed hunks."""

    path: str
    old_path: str = ""
    status: str = "modified"
    hunks: list[DiffHunk] = field(default_factory=list)
    added_lines: int = 0
    deleted_lines: int = 0
    candidate_lines: list[int] = field(default_factory=list)
    is_binary: bool = False


@dataclass
class InputSummary(DictMixin):
    """SQLite-friendly summary of review input."""

    task_id: str
    input_type: InputType
    input_ref: str
    changed_files: list[ChangedFile] = field(default_factory=list)
    raw_diff_sha256: str = ""
    file_count: int = 0
    hunk_count: int = 0
    added_lines: int = 0
    deleted_lines: int = 0
    summary: str = ""
    diagnostics: list[str] = field(default_factory=list)
    parser_version: str = "code-review.input.v1"
    warnings: list[str] = field(default_factory=list)


@dataclass
class Finding(DictMixin):
    """A structured code review finding."""

    severity: FindingSeverity
    category: FindingCategory
    file: str
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: FindingSource
    line: int | None = None
    fingerprint: str | None = None


@dataclass
class FilterEvent(DictMixin):
    """A governance decision recorded before rule or sandbox execution."""

    task_id: str
    decision: FilterDecision
    reason: str
    target: str
    id: str = field(default_factory=lambda: new_id("filter-event"))
    reason_code: FilterReasonCode = FilterReasonCode.UNKNOWN
    target_type: FilterTargetType = FilterTargetType.COMMAND
    command: str = ""
    runtime: RuntimeKind | None = None
    cwd: str = ""
    tool_name: str = ""
    skill_name: str = ""
    script_path: str = ""
    path: str = ""
    network_host: str = ""
    timeout_sec: float | None = None
    output_limit_bytes: int | None = None
    budget_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class SandboxRun(DictMixin):
    """A single sandbox command execution record."""

    task_id: str
    runtime: RuntimeKind
    command: str
    id: str = field(default_factory=lambda: new_id("sandbox-run"))
    exit_code: int | None = None
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    status: SandboxStatus = SandboxStatus.SUCCESS
    timeout_sec: float | None = None
    output_limit_bytes: int | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error_type: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class ReviewMetrics(DictMixin):
    """Operational metrics collected during a review."""

    total_duration_ms: int = 0
    sandbox_duration_ms: int = 0
    tool_call_count: int = 0
    interception_count: int = 0
    finding_count: int = 0
    warning_count: int = 0
    needs_human_review_count: int = 0
    severity_distribution: dict[str, int] = field(default_factory=dict)
    category_distribution: dict[str, int] = field(default_factory=dict)
    exception_distribution: dict[str, int] = field(default_factory=dict)


@dataclass
class ReviewReport(DictMixin):
    """Final machine-readable review report contract."""

    task_id: str
    findings: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    needs_human_review: list[Finding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    metrics: ReviewMetrics = field(default_factory=ReviewMetrics)
    interceptions: list[FilterEvent] = field(default_factory=list)
    sandbox_runs: list[SandboxRun] = field(default_factory=list)
    conclusion: str = ""
