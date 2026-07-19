"""Secret redaction helpers for logs, reports, and persistence."""

from __future__ import annotations

import re
from dataclasses import replace

from .review_types import (
    ReviewFinding,
    ReviewReport,
    ReviewTask,
    SandboxRunRecord,
)

_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*['\"]?)([^'\"\s]{4,})(['\"]?)"),
        r"\1\2[REDACTED]\4",
    ),
    (
        re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._\-]{6,})"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "[REDACTED_AWS_KEY]",
    ),
    (
        re.compile(r"sk-[A-Za-z0-9]{8,}"),
        "[REDACTED_OPENAI_KEY]",
    ),
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
]


def redact_text(text: str) -> str:
    """Redact sensitive material from arbitrary text."""

    redacted = text
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_finding(finding: ReviewFinding) -> ReviewFinding:
    """Return a copy of a finding with redacted evidence and recommendation."""

    return replace(
        finding,
        evidence=redact_text(finding.evidence),
        recommendation=redact_text(finding.recommendation),
        title=redact_text(finding.title),
    )


def redact_sandbox_run(record: SandboxRunRecord) -> SandboxRunRecord:
    """Return a copy of a sandbox run with redacted command output."""

    return replace(
        record,
        command=[redact_text(part) for part in record.command],
        stdout=redact_text(record.stdout),
        stderr=redact_text(record.stderr),
    )


def redact_task(task: ReviewTask) -> ReviewTask:
    """Return a copy of the task with redacted output-bearing fields."""

    return replace(
        task,
        findings=[redact_finding(finding) for finding in task.findings],
        sandbox_runs=[redact_sandbox_run(run) for run in task.sandbox_runs],
        error_message=redact_text(task.error_message) if task.error_message else None,
    )


def redact_report(report: ReviewReport) -> ReviewReport:
    """Return a copy of the report with all external-facing text redacted."""

    return replace(
        report,
        findings=[redact_finding(finding) for finding in report.findings],
        warnings=[redact_finding(finding) for finding in report.warnings],
        needs_human_review=[
            redact_finding(finding) for finding in report.needs_human_review
        ],
        sandbox_runs=[redact_sandbox_run(run) for run in report.sandbox_runs],
        summary=redact_text(report.summary),
    )
