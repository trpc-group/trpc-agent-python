"""Structured data exchanged by the Agent, storage, and reporters."""

from datetime import datetime
from enum import Enum
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator


class ReviewScope(str, Enum):
    """Supported review scopes."""

    CHANGED = "changed"
    FULL = "full"


class ReviewInputSummary(BaseModel):
    """Persistable summary of the reviewed input."""

    kind: Literal["diff_file", "file_list", "git_worktree", "fixture"]
    source: str = Field(max_length=1024)
    digest: str = Field(max_length=128)
    review_profile: str = Field(default="legacy", max_length=128)
    file_count: int = 0
    hunk_count: int = 0
    added_lines: int = 0
    removed_lines: int = 0
    files: list[str] = Field(default_factory=list, max_length=1000)
    redacted_preview: str = Field(default="", max_length=2000)


class FilterDecision(BaseModel):
    """One pre-execution policy decision."""

    decision_id: str
    command: str = Field(max_length=4096)
    decision: Literal["allow", "deny", "needs_human_review"]
    reason: str = Field(max_length=2000)
    created_at: datetime


class SandboxRun(BaseModel):
    """Auditable summary of one sandbox execution attempt."""

    run_id: str
    command: str = Field(max_length=4096)
    status: Literal["success", "failed", "timeout", "blocked", "simulated"]
    duration_ms: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False
    output_truncated: bool = False
    stdout_summary: str = Field(default="", max_length=2000)
    stderr_summary: str = Field(default="", max_length=2000)
    error_type: str | None = Field(default=None, max_length=200)


class MonitoringSummary(BaseModel):
    """Metrics collected for one review task."""

    total_duration_ms: float = 0.0
    sandbox_duration_ms: float = 0.0
    tool_call_count: int = 0
    blocked_count: int = 0
    finding_count: int = 0
    severity_distribution: dict[str, int] = Field(default_factory=dict)
    exception_distribution: dict[str, int] = Field(default_factory=dict)


class ReviewFinding(BaseModel):
    """One evidence-backed code review finding."""

    severity: Literal["critical", "high", "medium", "low"]
    category: str = Field(max_length=100)
    file: str = Field(max_length=1024)
    line: Optional[int] = Field(default=None, ge=1)
    title: str = Field(max_length=300)
    evidence: str = Field(max_length=4000)
    recommendation: str = Field(max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = Field(max_length=200)

    @field_validator("line", mode="before")
    @classmethod
    def normalize_unknown_line(cls, value: object) -> object:
        """Accept common model sentinels while persisting unknown lines as null."""
        if isinstance(value, (int, float)) and value <= 0:
            return None
        if isinstance(value, str) and value.strip() in {"", "0", "-1", "null", "None"}:
            return None
        return value


class ReviewAnalysis(BaseModel):
    """Structured response produced by the reasoning Agent."""

    summary: str = Field(max_length=4000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=500)
    warnings: list[ReviewFinding] = Field(default_factory=list, max_length=500)
    needs_human_review: list[ReviewFinding] = Field(default_factory=list, max_length=500)
    checks_performed: list[str] = Field(default_factory=list, max_length=200)


class ReviewReport(BaseModel):
    """Completed report with workflow metadata."""

    task_id: str
    created_at: datetime
    completed_at: datetime
    status: Literal["completed", "completed_with_warnings", "failed"]
    repository: str = Field(max_length=2048)
    scope: ReviewScope
    input_summary: ReviewInputSummary
    analysis: ReviewAnalysis
    filter_decisions: list[FilterDecision] = Field(default_factory=list)
    sandbox_runs: list[SandboxRun] = Field(default_factory=list)
    monitoring: MonitoringSummary = Field(default_factory=MonitoringSummary)
    conclusion: str = Field(max_length=4000)
