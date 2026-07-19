"""Telemetry summary helpers for review metrics."""

from __future__ import annotations

from collections import Counter

from .review_types import FindingDisposition, ParsedDiff, ReviewFinding, ReviewTask


def build_monitoring_summary(
    *,
    task: ReviewTask,
    parsed_diff: ParsedDiff,
    total_duration_ms: int,
) -> dict[str, int | float | str]:
    """Build the monitoring summary required by the review report."""

    findings = task.findings
    severity_distribution = Counter(
        finding.severity.value
        for finding in findings
        if finding.disposition == FindingDisposition.FINDING
    )
    category_distribution = Counter(finding.category.value for finding in findings)
    exception_distribution = Counter()
    if task.error_message:
        exception_distribution["pipeline_error"] += 1

    return {
        "total_duration_ms": total_duration_ms,
        "changed_files_count": parsed_diff.changed_files_count,
        "added_lines_count": parsed_diff.added_lines_count,
        "deleted_lines_count": parsed_diff.deleted_lines_count,
        "sandbox_run_count": len(task.sandbox_runs),
        "filter_decision_count": len(task.filter_decisions),
        "finding_count": _count_by_disposition(findings, FindingDisposition.FINDING),
        "needs_human_review_count": _count_by_disposition(
            findings,
            FindingDisposition.NEEDS_HUMAN_REVIEW,
        ),
        "warning_count": _count_by_disposition(findings, FindingDisposition.WARNING),
        "severity_distribution": dict(severity_distribution),
        "category_distribution": dict(category_distribution),
        "exception_distribution": dict(exception_distribution),
    }


def _count_by_disposition(
    findings: list[ReviewFinding],
    disposition: FindingDisposition,
) -> int:
    """Count findings by output bucket."""

    return sum(1 for finding in findings if finding.disposition == disposition)
