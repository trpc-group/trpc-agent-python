"""Shared review data models for the skills code review example."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ReviewInputKind(str, Enum):
    """Supported review input sources."""

    DIFF_FILE = "diff_file"
    REPO_PATH = "repo_path"
    FIXTURE = "fixture"


class DiffLineType(str, Enum):
    """Line types inside a unified diff hunk."""

    CONTEXT = "context"
    ADD = "add"
    DELETE = "delete"


class ReviewStatus(str, Enum):
    """Lifecycle states for a review task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewSeverity(str, Enum):
    """Supported finding severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReviewCategory(str, Enum):
    """High-level finding categories used by the review agent."""

    SECURITY = "security"
    ASYNC = "async"
    RESOURCE_LEAK = "resource_leak"
    TEST_MISSING = "test_missing"
    SECRET = "secret"
    DB_LIFECYCLE = "db_lifecycle"
    SANDBOX = "sandbox"
    GENERAL = "general"


class FindingSource(str, Enum):
    """Sources that can produce review findings."""

    RULE_ENGINE = "rule_engine"
    SKILL_SCRIPT = "skill_script"
    SANDBOX = "sandbox"
    FILTER = "filter"
    MODEL = "model"


class FindingDisposition(str, Enum):
    """How a finding should be presented downstream."""

    FINDING = "finding"
    WARNING = "warning"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class SandboxRunStatus(str, Enum):
    """Execution status for a sandbox run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"


class FilterDecisionType(str, Enum):
    """Supported filter outcomes before sandbox execution."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class ReviewConclusion(str, Enum):
    """Final review verdict for reporting."""

    PASS = "pass"
    FAIL = "fail"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    ERROR = "error"


@dataclass(slots=True, frozen=True)
class ReviewInput:
    """Normalized review input before parsing."""

    kind: ReviewInputKind
    source: str
    diff_text: str
    repo_path: Path | None = None


@dataclass(slots=True, frozen=True)
class DiffLine:
    """Single parsed line inside a unified diff hunk."""

    line_type: DiffLineType
    text: str
    raw_line: str
    old_line_no: int | None
    new_line_no: int | None


@dataclass(slots=True)
class DiffHunk:
    """A parsed unified diff hunk."""

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def added_line_numbers(self) -> list[int]:
        """Return new-file line numbers added in this hunk."""
        return [
            line.new_line_no
            for line in self.lines
            if line.line_type == DiffLineType.ADD and line.new_line_no is not None
        ]

    @property
    def deleted_line_numbers(self) -> list[int]:
        """Return old-file line numbers deleted in this hunk."""
        return [
            line.old_line_no
            for line in self.lines
            if line.line_type == DiffLineType.DELETE and line.old_line_no is not None
        ]

    def candidate_line_numbers(self, radius: int = 1) -> list[int]:
        """Return candidate new-file line numbers for rule checks.

        Candidate lines include added lines and their nearby context lines in the
        resulting file so rules can inspect a small amount of surrounding code.
        """

        if radius < 0:
            raise ValueError("radius must be >= 0")

        numbers: set[int] = set(self.added_line_numbers)
        if radius == 0:
            return sorted(numbers)

        line_count = len(self.lines)
        for index, line in enumerate(self.lines):
            if line.line_type != DiffLineType.ADD:
                continue

            numbers.update(
                _collect_neighbor_new_lines(
                    lines=self.lines,
                    start_index=index,
                    direction=-1,
                    limit=radius,
                )
            )
            numbers.update(
                _collect_neighbor_new_lines(
                    lines=self.lines,
                    start_index=index,
                    direction=1,
                    limit=radius,
                )
            )

        return sorted(numbers)


@dataclass(slots=True)
class ChangedFile:
    """A changed file in a parsed diff."""

    old_path: str
    new_path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted_file: bool = False
    is_rename: bool = False

    @property
    def display_path(self) -> str:
        """Return the most useful path for user-facing output."""
        return self.new_path if self.new_path != "/dev/null" else self.old_path

    @property
    def added_line_numbers(self) -> list[int]:
        """Return all added line numbers across hunks."""
        numbers = {
            line_no
            for hunk in self.hunks
            for line_no in hunk.added_line_numbers
        }
        return sorted(numbers)

    @property
    def deleted_line_numbers(self) -> list[int]:
        """Return all deleted line numbers across hunks."""
        numbers = {
            line_no
            for hunk in self.hunks
            for line_no in hunk.deleted_line_numbers
        }
        return sorted(numbers)

    def candidate_line_numbers(self, radius: int = 1) -> list[int]:
        """Return deduplicated candidate line numbers across hunks."""
        numbers = {
            line_no
            for hunk in self.hunks
            for line_no in hunk.candidate_line_numbers(radius=radius)
        }
        return sorted(numbers)


@dataclass(slots=True)
class ParsedDiff:
    """Top-level parsed diff structure."""

    raw_diff: str
    files: list[ChangedFile] = field(default_factory=list)

    @property
    def changed_files_count(self) -> int:
        """Return the number of changed files."""
        return len(self.files)

    @property
    def added_lines_count(self) -> int:
        """Return the total number of added lines."""
        return sum(len(diff_file.added_line_numbers) for diff_file in self.files)

    @property
    def deleted_lines_count(self) -> int:
        """Return the total number of deleted lines."""
        return sum(len(diff_file.deleted_line_numbers) for diff_file in self.files)

    @property
    def changed_paths(self) -> list[str]:
        """Return changed file paths suitable for reporting."""
        return [diff_file.display_path for diff_file in self.files]


@dataclass(slots=True)
class ReviewFinding:
    """Structured review finding emitted by rules, filters, or sandbox runs."""

    severity: ReviewSeverity
    category: ReviewCategory
    file: str
    line: int | None
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: FindingSource
    disposition: FindingDisposition = FindingDisposition.FINDING
    fingerprint: str | None = None

    def __post_init__(self) -> None:
        """Validate normalized confidence range."""

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass(slots=True)
class SandboxRunRecord:
    """Audit record for a single sandboxed command or script execution."""

    name: str
    command: list[str]
    status: SandboxRunStatus
    runtime: str
    duration_ms: int = 0
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    output_truncated: bool = False
    blocked_by_filter: bool = False


@dataclass(slots=True)
class FilterDecisionRecord:
    """Structured record for a pre-execution filter decision."""

    decision: FilterDecisionType
    target: str
    reason_code: str
    reason: str
    requires_human_review: bool = False


@dataclass(slots=True)
class ReviewTask:
    """Top-level review task and its execution state."""

    task_id: str
    status: ReviewStatus
    review_input: ReviewInput
    parsed_diff: ParsedDiff | None = None
    findings: list[ReviewFinding] = field(default_factory=list)
    sandbox_runs: list[SandboxRunRecord] = field(default_factory=list)
    filter_decisions: list[FilterDecisionRecord] = field(default_factory=list)
    error_message: str | None = None

    def add_finding(self, finding: ReviewFinding) -> None:
        """Append a new finding to the task."""

        self.findings.append(finding)

    def add_sandbox_run(self, sandbox_run: SandboxRunRecord) -> None:
        """Append a sandbox execution record."""

        self.sandbox_runs.append(sandbox_run)

    def add_filter_decision(self, decision: FilterDecisionRecord) -> None:
        """Append a filter decision record."""

        self.filter_decisions.append(decision)


@dataclass(slots=True)
class ReviewReport:
    """Final structured report returned by the review pipeline."""

    task_id: str
    conclusion: ReviewConclusion
    findings: list[ReviewFinding] = field(default_factory=list)
    warnings: list[ReviewFinding] = field(default_factory=list)
    needs_human_review: list[ReviewFinding] = field(default_factory=list)
    filter_decisions: list[FilterDecisionRecord] = field(default_factory=list)
    sandbox_runs: list[SandboxRunRecord] = field(default_factory=list)
    summary: str = ""
    severity_counts: dict[str, int] = field(default_factory=dict)
    monitoring_summary: dict[str, int | float | str] = field(default_factory=dict)

    @classmethod
    def from_task(
        cls,
        *,
        task: ReviewTask,
        conclusion: ReviewConclusion,
        summary: str = "",
        monitoring_summary: dict[str, int | float | str] | None = None,
    ) -> "ReviewReport":
        """Build a report by splitting findings according to disposition."""

        findings: list[ReviewFinding] = []
        warnings: list[ReviewFinding] = []
        needs_human_review: list[ReviewFinding] = []

        for finding in task.findings:
            if finding.disposition == FindingDisposition.WARNING:
                warnings.append(finding)
            elif finding.disposition == FindingDisposition.NEEDS_HUMAN_REVIEW:
                needs_human_review.append(finding)
            else:
                findings.append(finding)

        severity_counts: dict[str, int] = {}
        for finding in findings:
            key = finding.severity.value
            severity_counts[key] = severity_counts.get(key, 0) + 1

        return cls(
            task_id=task.task_id,
            conclusion=conclusion,
            findings=findings,
            warnings=warnings,
            needs_human_review=needs_human_review,
            filter_decisions=list(task.filter_decisions),
            sandbox_runs=list(task.sandbox_runs),
            summary=summary,
            severity_counts=severity_counts,
            monitoring_summary=monitoring_summary or {},
        )


def _collect_neighbor_new_lines(
    *,
    lines: list[DiffLine],
    start_index: int,
    direction: int,
    limit: int,
) -> set[int]:
    """Collect nearby new-file line numbers while skipping deleted lines."""

    collected: set[int] = set()
    index = start_index + direction
    while 0 <= index < len(lines) and len(collected) < limit:
        line = lines[index]
        if line.new_line_no is not None:
            collected.add(line.new_line_no)
        index += direction
    return collected
