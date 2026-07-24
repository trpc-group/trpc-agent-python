# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report builders for the code review dry-run example."""

from __future__ import annotations

from pathlib import Path

from .schemas import AuditEvent
from .schemas import FilterDecision
from .schemas import ParsedDiff
from .schemas import ReviewFinding
from .schemas import ReviewInput
from .schemas import ReviewMetrics
from .schemas import ReviewReport
from .schemas import ReviewTaskStatus
from .schemas import SandboxRun

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def build_report(
    parsed_diff: ParsedDiff,
    findings: list[ReviewFinding],
    warnings: list[ReviewFinding],
    decisions: list[FilterDecision],
    *,
    task_id: str | None = None,
    status: ReviewTaskStatus = ReviewTaskStatus.COMPLETED,
    review_input: ReviewInput | None = None,
    sandbox_runs: list[SandboxRun] | None = None,
    audit_events: list[AuditEvent] | None = None,
    duration_ms: int = 0,
    redaction_count: int = 0,
) -> ReviewReport:
    """Build a structured dry-run review report."""
    sandbox_runs = sandbox_runs or []
    audit_events = audit_events or []
    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for finding in findings:
        severity_counts[finding.severity.value] = severity_counts.get(finding.severity.value, 0) + 1
        category_counts[finding.category] = category_counts.get(finding.category, 0) + 1
    for warning in warnings:
        category_counts[warning.category] = category_counts.get(warning.category, 0) + 1

    exception_counts: dict[str, int] = {}
    for run in sandbox_runs:
        if run.error_type:
            exception_counts[run.error_type] = exception_counts.get(run.error_type, 0) + 1

    metrics = ReviewMetrics(
        file_count=len(parsed_diff.files),
        hunk_count=parsed_diff.hunk_count,
        changed_line_count=parsed_diff.changed_line_count,
        finding_count=len(findings),
        warning_count=len(warnings),
        severity_counts=severity_counts,
        category_counts=category_counts,
        duration_ms=duration_ms,
        sandbox_duration_ms=sum(run.duration_ms for run in sandbox_runs),
        tool_call_count=len(sandbox_runs),
        sandbox_run_count=len(sandbox_runs),
        filter_intercept_count=sum(1 for decision in decisions if decision.decision in {"deny", "needs_human_review"}),
        redaction_count=redaction_count,
        exception_counts=exception_counts,
    )
    if findings:
        summary = f"Found {len(findings)} finding(s) across {len(parsed_diff.files)} changed file(s)."
    else:
        summary = f"No findings found across {len(parsed_diff.files)} changed file(s)."
    if warnings:
        final_conclusion = "Review completed with warnings that need human review."
        if status == ReviewTaskStatus.COMPLETED:
            status = ReviewTaskStatus.COMPLETED_WITH_WARNINGS
    elif findings:
        final_conclusion = "Review completed with high-confidence findings."
    else:
        final_conclusion = "Review completed with no high-confidence findings."
    return ReviewReport(
        task_id=task_id,
        status=status,
        input=review_input,
        summary=summary,
        findings=_sort_findings(findings),
        warnings=_sort_findings(warnings),
        filter_decisions=sorted(decisions, key=_decision_sort_key),
        metrics=metrics,
        sandbox_runs=sorted(sandbox_runs, key=lambda run: (run.script_name, run.id)),
        audit_events=audit_events,
        final_conclusion=final_conclusion,
    )


def report_to_json(report: ReviewReport) -> str:
    """Serialize a report as formatted JSON."""
    return report.model_dump_json(indent=2)


def render_markdown_report(report: ReviewReport) -> str:
    """Render a deterministic Markdown report."""
    lines: list[str] = [
        "# Code Review Dry-run Report",
        "",
        f"**Mode:** `{report.mode}`",
        f"**Status:** `{report.status.value}`",
    ]
    if report.task_id:
        lines.append(f"**Task ID:** `{report.task_id}`")
    lines.extend(
        [
            "",
            f"## Summary\n\n{report.summary}",
            "",
            f"## Final conclusion\n\n{report.final_conclusion}",
            "",
            "## Metrics",
            "",
            f"- Files reviewed: {report.metrics.file_count}",
            f"- Hunks reviewed: {report.metrics.hunk_count}",
            f"- Changed lines reviewed: {report.metrics.changed_line_count}",
            f"- Findings: {report.metrics.finding_count}",
            f"- Warnings / needs human review: {report.metrics.warning_count}",
            f"- Duration ms: {report.metrics.duration_ms}",
            f"- Sandbox duration ms: {report.metrics.sandbox_duration_ms}",
            f"- Sandbox runs: {report.metrics.sandbox_run_count}",
            f"- Tool calls: {report.metrics.tool_call_count}",
            f"- Filter intercepts: {report.metrics.filter_intercept_count}",
            f"- Redactions: {report.metrics.redaction_count}",
            "",
            "## Severity distribution",
            "",
        ]
    )

    if report.metrics.severity_counts:
        for severity in sorted(report.metrics.severity_counts, key=lambda item: _SEVERITY_ORDER.get(item, 99)):
            lines.append(f"- {severity}: {report.metrics.severity_counts[severity]}")
    else:
        lines.append("- none: 0")

    lines.extend(["", "## Category distribution", ""])
    if report.metrics.category_counts:
        for category in sorted(report.metrics.category_counts):
            lines.append(f"- {category}: {report.metrics.category_counts[category]}")
    else:
        lines.append("- none: 0")

    lines.extend(["", "## Findings", ""])
    if not report.findings:
        lines.append("No high-confidence findings.")
    else:
        for index, finding in enumerate(report.findings, start=1):
            lines.extend(
                [
                    f"### {index}. {finding.title}",
                    "",
                    f"- Severity: `{finding.severity.value}`",
                    f"- Category: `{finding.category}`",
                    f"- Location: `{finding.file}:{finding.line}`",
                    f"- Confidence: `{finding.confidence.value}`",
                    f"- Source: `{finding.source.value}`",
                    f"- Fingerprint: `{finding.fingerprint or ''}`",
                    "",
                    f"Evidence: {finding.evidence}",
                    "",
                    f"Recommendation: {finding.recommendation}",
                    "",
                ]
            )

    lines.extend(["", "## Warnings / needs human review", ""])
    if not report.warnings:
        lines.append("No warnings.")
    else:
        for warning in report.warnings:
            lines.append(f"- `{warning.file}:{warning.line}` {warning.title} ({warning.category}, {warning.confidence.value})")

    lines.extend(["", "## Filter governance summary", ""])
    if not report.filter_decisions:
        lines.append("No filter decisions recorded.")
    else:
        for decision in report.filter_decisions:
            location = f" {decision.file}:{decision.line}" if decision.file and decision.line else ""
            script = f" [{decision.script_name}]" if decision.script_name else ""
            lines.append(
                f"- `{decision.stage}` `{decision.filter_name}` -> `{decision.decision}`{script}{location}: {decision.reason}"
            )

    lines.extend(["", "## Sandbox execution summary", ""])
    if not report.sandbox_runs:
        lines.append("No sandbox runs recorded.")
    else:
        for run in report.sandbox_runs:
            status = "timeout" if run.timed_out else f"exit {run.exit_code}"
            lines.append(
                f"- `{run.script_name}` on `{run.runtime}`: {status}, {run.duration_ms} ms, "
                f"truncated={str(run.output_truncated).lower()}"
            )
            if run.stderr_excerpt:
                lines.append(f"  - stderr: {run.stderr_excerpt}")

    lines.extend(["", "## Audit / exceptions", ""])
    if report.metrics.exception_counts:
        for error_type in sorted(report.metrics.exception_counts):
            lines.append(f"- {error_type}: {report.metrics.exception_counts[error_type]}")
    elif not report.audit_events:
        lines.append("No exceptions recorded.")
    for event in report.audit_events:
        lines.append(f"- `{event.severity}` {event.event_type}: {event.message}")

    lines.extend(["", "## Actionable recommendations", ""])
    if report.findings:
        for finding in report.findings:
            lines.append(f"- `{finding.file}:{finding.line}` {finding.recommendation}")
    elif report.warnings:
        lines.append("- Review the human-review warnings before merging.")
    else:
        lines.append("- No immediate action required by the dry-run reviewer.")

    return "\n".join(lines).rstrip() + "\n"


def write_report_files(report: ReviewReport, output_dir: Path) -> tuple[Path, Path]:
    """Write JSON and Markdown reports to an output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "review_report.json"
    markdown_path = output_dir / "review_report.md"
    json_path.write_text(report_to_json(report) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, markdown_path


def _sort_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    return sorted(
        findings,
        key=lambda finding: (
            _SEVERITY_ORDER.get(finding.severity.value, 99),
            finding.file,
            finding.line,
            finding.category,
            finding.title,
        ),
    )


def _decision_sort_key(decision: FilterDecision) -> tuple[str, str, str, int, str]:
    return (
        decision.stage,
        decision.filter_name,
        decision.file or "",
        decision.line or 0,
        decision.script_name or "",
    )
