#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Core models, input handling, redaction, and finding normalization."""

from __future__ import annotations

import difflib
import hashlib
import re
import shlex
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

MAX_DIFF_BYTES = 2 * 1024 * 1024
CONFIDENCE_THRESHOLD = 0.80
SEVERITY_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}
_SECRET_KEY_NAME = (
    r"(?:[a-z0-9]+[_-])*"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|pwd)"
)
_SECRET_KEY_PREFIX = rf"(?:(?:\b{_SECRET_KEY_NAME}\b)|(?:[\"']{_SECRET_KEY_NAME}[\"']))\s*[:=]\s*"


class Finding(BaseModel):
    """A normalized review finding."""

    severity: str
    category: str
    file: str
    line: int = Field(ge=0)
    title: str
    evidence: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str


class InputSummary(BaseModel):
    """Non-sensitive summary of the review input."""

    input_type: str
    sha256: str
    byte_count: int
    file_count: int
    hunk_count: int
    changed_line_count: int
    files: list[str]
    diff_preview: str


class FilterDecision(BaseModel):
    """Audit record emitted before a sandbox is created."""

    decision: str
    rule_id: str
    reason: str
    script: str
    network_hosts: list[str] = Field(default_factory=list)


class SandboxRun(BaseModel):
    """Bounded sandbox execution summary."""

    runtime: str
    status: str
    command: str
    duration_ms: int = 0
    exit_code: Optional[int] = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    error_type: str = ""
    skill_loaded: bool = False


class MonitoringSummary(BaseModel):
    """Metrics persisted with every review."""

    total_duration_ms: int = 0
    sandbox_duration_ms: int = 0
    tool_calls: int = 0
    interception_count: int = 0
    finding_count: int = 0
    warning_count: int = 0
    severity_distribution: dict[str, int] = Field(default_factory=dict)
    exception_distribution: dict[str, int] = Field(default_factory=dict)
    redaction_count: int = 0


class ReviewReport(BaseModel):
    """Complete machine-readable review report."""

    task_id: str
    status: str
    model_mode: str
    input_summary: InputSummary
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[Finding] = Field(default_factory=list)
    needs_human_review: list[str] = Field(default_factory=list)
    filter_decisions: list[FilterDecision] = Field(default_factory=list)
    sandbox_runs: list[SandboxRun] = Field(default_factory=list)
    monitoring: MonitoringSummary = Field(default_factory=MonitoringSummary)
    operational_warnings: list[str] = Field(default_factory=list)
    conclusion: str = ""
    generated_at: str


class ResolvedInput(BaseModel):
    """Resolved review input before sandbox staging."""

    input_type: str
    diff_text: str
    summary: InputSummary


class SecretRedactor:
    """Redact common credentials from every persistence and reporting path."""

    _known_tokens = (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
        re.compile(r"\b(?:sk|rk)-(?:live|test|proj)-[A-Za-z0-9_-]{8,}"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    )
    _assignment = re.compile(
        rf"""(?ix)
        ({_SECRET_KEY_PREFIX})
        ("[^"\r\n]{{4,}}"|'[^'\r\n]{{4,}}'|[^"'\s,;}}]{{4,}})
        """
    )
    _url_credentials = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@\s/]+)(@)")
    _private_key = re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.DOTALL,
    )
    _private_key_marker = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")

    @classmethod
    def redact_text(cls, value: str) -> tuple[str, int]:
        """Return redacted text and replacement count."""

        redacted = value
        count = 0
        for pattern in cls._known_tokens:
            redacted, replaced = pattern.subn("[REDACTED]", redacted)
            count += replaced
        redacted, replaced = cls._assignment.subn(cls._redact_assignment, redacted)
        count += replaced
        redacted, replaced = cls._url_credentials.subn(r"\1[REDACTED]\3", redacted)
        count += replaced
        redacted, replaced = cls._private_key.subn("[REDACTED_PRIVATE_KEY]", redacted)
        count += replaced
        redacted, replaced = cls._private_key_marker.subn("[REDACTED_PRIVATE_KEY]", redacted)
        count += replaced
        return redacted, count

    @staticmethod
    def _redact_assignment(match: re.Match[str]) -> str:
        value = match.group(2)
        quote = value[0] if value[0] in {'"', "'"} else ""
        return f"{match.group(1)}{quote}[REDACTED]{quote}"

    @classmethod
    def redact_value(cls, value: Any) -> tuple[Any, int]:
        """Recursively redact strings in structured values."""

        if isinstance(value, str):
            return cls.redact_text(value)
        if isinstance(value, list):
            output = []
            count = 0
            for item in value:
                clean, item_count = cls.redact_value(item)
                output.append(clean)
                count += item_count
            return output, count
        if isinstance(value, tuple):
            output, count = cls.redact_value(list(value))
            return tuple(output), count
        if isinstance(value, dict):
            output = {}
            count = 0
            for key, item in value.items():
                clean, item_count = cls.redact_value(item)
                output[key] = clean
                count += item_count
            return output, count
        return value, 0


class InputResolver:
    """Resolve unified diffs, selected files, or git workspace changes."""

    def __init__(self, max_diff_bytes: int = MAX_DIFF_BYTES):
        self.max_diff_bytes = max_diff_bytes

    def resolve_diff_file(self, path: Path, input_type: str = "diff_file") -> ResolvedInput:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"diff file not found: {path}")
        raw = path.read_bytes()
        if len(raw) > self.max_diff_bytes:
            raise ValueError(f"diff exceeds {self.max_diff_bytes} byte input limit")
        text = raw.decode("utf-8", errors="replace")
        return ResolvedInput(input_type=input_type, diff_text=text, summary=self.summarize(text, input_type))

    def resolve_repo(self, repo_path: Path, files: Optional[list[str]] = None) -> ResolvedInput:
        repo = repo_path.expanduser().resolve()
        if not repo.is_dir():
            raise ValueError(f"not a git working tree: {repo}")
        try:
            is_work_tree = self._run_git(repo, ["rev-parse", "--is-inside-work-tree"]).strip()
        except RuntimeError as error:
            raise ValueError(f"not a git working tree: {repo}") from error
        if is_work_tree != "true":
            raise ValueError(f"not a git working tree: {repo}")

        path_args = ["--", *files] if files else []
        diff = self._run_git(repo, ["diff", "--no-ext-diff", "--unified=3", "HEAD", *path_args])
        untracked = self._run_git(repo, ["ls-files", "--others", "--exclude-standard", *path_args]).splitlines()
        selected = set(files or [])
        for relative in untracked:
            if selected and relative not in selected:
                continue
            diff += self._new_file_diff(repo, relative)

        encoded = diff.encode("utf-8")
        if len(encoded) > self.max_diff_bytes:
            raise ValueError(f"workspace diff exceeds {self.max_diff_bytes} byte input limit")
        return ResolvedInput(input_type="git_workspace", diff_text=diff,
                             summary=self.summarize(diff, "git_workspace"))

    def resolve_files(self, paths: list[Path]) -> ResolvedInput:
        if not paths:
            raise ValueError("at least one file path is required")
        chunks = []
        for path in paths:
            resolved = path.expanduser().resolve()
            if not resolved.is_file():
                raise FileNotFoundError(f"input file not found: {resolved}")
            content = resolved.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            relative = resolved.name
            chunks.append(f"diff --git a/{relative} b/{relative}\n")
            chunks.extend(
                difflib.unified_diff(
                    [],
                    content,
                    fromfile="/dev/null",
                    tofile=f"b/{relative}",
                    lineterm="\n",
                ))
        diff = "".join(chunks)
        if len(diff.encode("utf-8")) > self.max_diff_bytes:
            raise ValueError(f"selected files exceed {self.max_diff_bytes} byte input limit")
        return ResolvedInput(input_type="file_list", diff_text=diff, summary=self.summarize(diff, "file_list"))

    @staticmethod
    def _run_git(repo: Path, args: list[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result.stdout

    @staticmethod
    def _new_file_diff(repo: Path, relative: str) -> str:
        path = (repo / relative).resolve()
        if not path.is_relative_to(repo) or not path.is_file():
            return ""
        content = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        output = [f"diff --git a/{relative} b/{relative}\n", "new file mode 100644\n"]
        output.extend(
            difflib.unified_diff(
                [],
                content,
                fromfile="/dev/null",
                tofile=f"b/{relative}",
                lineterm="\n",
            ))
        return "".join(output)

    @staticmethod
    def summarize(diff_text: str, input_type: str) -> InputSummary:
        files: list[str] = []
        hunk_count = 0
        changed_line_count = 0
        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                try:
                    parts = shlex.split(line)
                    candidate = parts[3]
                except (ValueError, IndexError):
                    candidate = ""
                if candidate.startswith("b/"):
                    candidate = candidate[2:]
                if candidate and candidate not in files:
                    files.append(candidate)
            elif line.startswith("+++ "):
                header_path = line[4:].split("\t", 1)[0]
                try:
                    candidate = shlex.split(header_path)[0]
                except (ValueError, IndexError):
                    candidate = header_path
                if candidate.startswith("b/"):
                    candidate = candidate[2:]
                if candidate != "/dev/null" and candidate not in files:
                    files.append(candidate)
            elif line.startswith("@@ "):
                hunk_count += 1
            elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                changed_line_count += 1

        clean_preview, _ = SecretRedactor.redact_text(diff_text[:2000])
        return InputSummary(
            input_type=input_type,
            sha256=hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
            byte_count=len(diff_text.encode("utf-8")),
            file_count=len(files),
            hunk_count=hunk_count,
            changed_line_count=changed_line_count,
            files=files,
            diff_preview=clean_preview,
        )


def normalize_findings(raw_findings: Iterable[dict[str, Any]]) -> tuple[list[Finding], list[Finding], int]:
    """Validate, redact, deduplicate, and bucket scanner findings."""

    deduplicated: dict[tuple[str, int, str], Finding] = {}
    redaction_count = 0
    for raw in raw_findings:
        clean, count = SecretRedactor.redact_value(raw)
        redaction_count += count
        finding = Finding.model_validate(clean)
        key = (finding.file, finding.line, finding.category)
        previous = deduplicated.get(key)
        if previous is None:
            deduplicated[key] = finding
            continue
        previous_score = (SEVERITY_ORDER.get(previous.severity, 0), previous.confidence)
        current_score = (SEVERITY_ORDER.get(finding.severity, 0), finding.confidence)
        if current_score > previous_score:
            deduplicated[key] = finding

    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            -SEVERITY_ORDER.get(item.severity, 0),
            item.file,
            item.line,
            item.category,
        ),
    )
    findings = [item for item in ordered if item.confidence >= CONFIDENCE_THRESHOLD]
    warnings = [item for item in ordered if item.confidence < CONFIDENCE_THRESHOLD]
    return findings, warnings, redaction_count


def severity_distribution(findings: Iterable[Finding]) -> dict[str, int]:
    """Return stable severity counters."""

    counts = Counter(item.severity for item in findings)
    return {severity: counts[severity] for severity in SEVERITY_ORDER if counts[severity]}
