"""Data models for review task persistence."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ReviewTaskRecord:
    """Database record for a review task."""
    task_id: str
    diff_source: str = ""
    diff_summary: str = ""
    status: str = "pending"
    files_changed: int = 0
    total_findings: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    sandbox_runs: int = 0
    filter_intercepts: int = 0
    duration_ms: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class FindingRecord:
    """Database record for a single finding."""
    task_id: str
    severity: str
    category: str
    file: str
    line: int
    title: str
    evidence: str = ""
    recommendation: str = ""
    confidence: float = 0.0
    source: str = ""
    id: int | None = None


@dataclass
class SandboxRunRecord:
    """Database record for a sandbox execution."""
    task_id: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    output_truncated: bool = False
    error: str = ""
    id: int | None = None


@dataclass
class FilterLogRecord:
    """Database record for a filter interception."""
    task_id: str
    action: str
    reason: str = ""
    filter_name: str = ""
    id: int | None = None
