"""Shared conversion from validated reports to normalized storage rows."""

import hashlib

from reports.models import ReviewReport
from security import redact_text


def sandbox_rows(report: ReviewReport) -> list[tuple[object, ...]]:
    """Build backend-neutral sandbox audit rows."""
    return [
        (
            run.run_id,
            report.task_id,
            redact_text(run.command),
            run.status,
            run.duration_ms,
            run.exit_code,
            run.timed_out,
            run.output_truncated,
            redact_text(run.stdout_summary),
            redact_text(run.stderr_summary),
            run.error_type,
        )
        for run in report.sandbox_runs
    ]


def filter_decision_rows(report: ReviewReport) -> list[tuple[object, ...]]:
    """Build backend-neutral Filter decision rows."""
    return [
        (
            decision.decision_id,
            report.task_id,
            redact_text(decision.command),
            decision.decision,
            redact_text(decision.reason),
            decision.created_at.isoformat(),
        )
        for decision in report.filter_decisions
    ]


def finding_rows(report: ReviewReport) -> list[tuple[object, ...]]:
    """Build stable, idempotent rows for all confidence buckets."""
    rows = []
    for bucket, items in (
        ("finding", report.analysis.findings),
        ("warning", report.analysis.warnings),
        ("needs_human_review", report.analysis.needs_human_review),
    ):
        for finding in items:
            key = (
                f"{report.task_id}:{bucket}:{finding.file}:"
                f"{finding.line}:{finding.category}"
            )
            rows.append(
                (
                    hashlib.sha256(key.encode("utf-8")).hexdigest(),
                    report.task_id,
                    bucket,
                    finding.severity,
                    finding.category,
                    redact_text(finding.file),
                    finding.line,
                    redact_text(finding.title),
                    redact_text(finding.evidence),
                    redact_text(finding.recommendation),
                    finding.confidence,
                    redact_text(finding.source),
                )
            )
    return rows
