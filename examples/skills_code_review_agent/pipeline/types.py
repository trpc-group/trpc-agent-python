"""Shared data types for the code review pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(str, Enum):
    SECURITY = "security"
    ASYNC_ERROR = "async_error"
    RESOURCE_LEAK = "resource_leak"
    DB_LIFECYCLE = "db_lifecycle"
    MISSING_TESTS = "missing_tests"
    SECRET_INFO = "secret_info"


@dataclass
class DiffHunk:
    """A single hunk within a unified diff."""
    header: str                          # e.g. "@@ -10,6 +10,8 @@"
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


@dataclass
class DiffFile:
    """Parsed representation of one file in a diff."""
    filename: str
    old_filename: str = ""
    new_filename: str = ""
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class Finding:
    """A single review finding."""
    severity: Severity
    category: FindingCategory
    file: str
    line: int
    title: str
    evidence: str
    recommendation: str
    confidence: float       # 0.0 - 1.0
    source: str             # rule name or scanner that generated it

    def fingerprint(self) -> str:
        """Unique key for dedup: hash of (file, line, category, title)."""
        return f"{self.file}:{self.line}:{self.category.value}:{self.title}"


@dataclass
class FilterDecision:
    """Result of filter chain evaluation."""
    action: str             # "allow", "deny", "needs_human_review"
    reason: str
    filter_name: str = ""


@dataclass
class SandboxRun:
    """Record of a sandbox execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    output_truncated: bool = False
    error: str = ""


@dataclass
class ReviewTask:
    """Top-level review task record."""
    task_id: str
    diff_source: str       # file path or "stdin"
    diff_summary: str      # summary of changes
    status: str            # "completed", "failed", "filtered"
    files_changed: int
    total_findings: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    info_count: int
    sandbox_runs: int
    filter_intercepts: int
    duration_ms: int
    created_at: str = ""


@dataclass
class ReviewReport:
    """Complete review output."""
    task_id: str
    findings: list[Finding]
    filter_summary: dict
    sandbox_summary: dict
    telemetry: dict
    human_review_items: list[Finding]
    recommendations: list[str]
