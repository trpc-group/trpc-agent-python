# models.py —— 零依赖 dataclass（Filter 门禁时仅 import 此文件，不触发沙箱/规则模块）
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Bucket(str, Enum):
    FINDINGS = "findings"
    WARNINGS = "warnings"
    NEEDS_REVIEW = "needs_human_review"


@dataclass
class ChangedLine:
    file: str
    new_line: Optional[int]
    old_line: Optional[int]
    content: str


@dataclass
class Hunk:
    file: str
    old_start: int
    new_start: int
    added: list[ChangedLine] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)  # 用于生命周期 close 信号检测


@dataclass
class DiffFile:
    path: str
    status: str  # added/modified/deleted/renamed
    hunks: list[Hunk] = field(default_factory=list)
    added_lines: list[ChangedLine] = field(default_factory=list)


@dataclass
class Finding:
    severity: Severity
    category: str
    file: str
    line: Optional[int]
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: str  # rule|ast|sandbox|semgrep|llm|rule+llm
    rule_id: str
    bucket: Bucket = Bucket.FINDINGS
    finding_id: str = ""  # sha256[:16]，由 dedup 填充


@dataclass
class SandboxRun:
    runtime: str
    script: str
    status: str  # success/failed/timeout/blocked
    exit_code: Optional[int]
    stdout_redacted: str
    stderr_redacted: str
    truncated: bool
    error_type: Optional[str]
    duration_ms: int


@dataclass
class FilterDecision:
    stage: str
    decision: str  # allow/deny/needs_human_review
    reason: str
    command_redacted: str


@dataclass
class MonitoringSummary:
    total_duration_ms: int
    sandbox_duration_ms: int
    tool_call_count: int
    blocked_count: int
    finding_count: int
    severity_distribution: dict[str, int]
    exception_distribution: dict[str, int]


@dataclass
class ReviewReport:
    task_id: str
    status: str
    conclusion: str  # approve/changes_requested/needs_human_review/completed_with_warnings
    findings: list[Finding]
    warnings: list[Finding]
    needs_human_review: list[Finding]
    filter_decisions: list[FilterDecision]
    sandbox_runs: list[SandboxRun]
    monitoring: MonitoringSummary
    repository: str
    input_summary: str
