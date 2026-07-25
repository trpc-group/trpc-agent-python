#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""JSON and Markdown report rendering with a final redaction gate."""

from __future__ import annotations

import json
from pathlib import Path

from .core import Finding
from .core import ReviewReport
from .core import SecretRedactor


def sanitize_report(report: ReviewReport) -> ReviewReport:
    """Apply defense-in-depth redaction to the complete report."""

    clean, count = SecretRedactor.redact_value(report.model_dump())
    clean["monitoring"]["redaction_count"] += count
    return ReviewReport.model_validate(clean)


def write_reports(report: ReviewReport, output_dir: Path) -> tuple[Path, Path]:
    """Atomically write machine-readable and human-readable reports."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "review_report.json"
    markdown_path = output_dir / "review_report.md"
    json_text = json.dumps(report.model_dump(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown_text = render_markdown(report)

    json_tmp = json_path.with_suffix(".json.tmp")
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    json_tmp.write_text(json_text, encoding="utf-8")
    markdown_tmp.write_text(markdown_text, encoding="utf-8")
    json_tmp.replace(json_path)
    markdown_tmp.replace(markdown_path)
    return json_path, markdown_path


def render_markdown(report: ReviewReport) -> str:
    """Render all required review and audit sections."""

    lines = [
        "# Automated Code Review Report",
        "",
        f"- Task: `{report.task_id}`",
        f"- Status: `{report.status}`",
        f"- Model mode: `{report.model_mode}`",
        f"- Conclusion: **{report.conclusion}**",
        f"- Input SHA-256: `{report.input_summary.sha256}`",
        "",
        "## Findings Summary",
        "",
        f"- High-confidence findings: {len(report.findings)}",
        f"- Needs human review: {len(report.warnings)}",
        f"- Files changed: {report.input_summary.file_count}",
        f"- Changed lines: {report.input_summary.changed_line_count}",
        "",
        "| Severity | Count |",
        "| --- | ---: |",
    ]
    if report.monitoring.severity_distribution:
        for severity, count in report.monitoring.severity_distribution.items():
            lines.append(f"| {severity} | {count} |")
    else:
        lines.append("| none | 0 |")

    lines.extend(["", "## Findings", ""])
    if report.findings:
        for finding in report.findings:
            lines.extend(_render_finding(finding))
    else:
        lines.append("No high-confidence findings.")

    lines.extend(["", "## Human Review", ""])
    if report.warnings:
        for finding in report.warnings:
            lines.extend(_render_finding(finding))
    if report.needs_human_review:
        for reason in report.needs_human_review:
            lines.append(f"- {reason}")
    if not report.warnings and not report.needs_human_review:
        lines.append("No items require human review.")

    lines.extend([
        "",
        "## Filter Governance",
        "",
        "| Decision | Rule | Script | Reason |",
        "| --- | --- | --- | --- |",
    ])
    if report.filter_decisions:
        for decision in report.filter_decisions:
            lines.append(
                f"| {decision.decision} | `{decision.rule_id}` | `{decision.script}` | "
                f"{_table_text(decision.reason)} |"
            )
    else:
        lines.append("| none | - | - | No execution was requested. |")

    lines.extend([
        "",
        "## Sandbox Execution",
        "",
        "| Runtime | Status | Duration (ms) | Exit | Timed out | Output truncated |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ])
    if report.sandbox_runs:
        for run in report.sandbox_runs:
            exit_code = "-" if run.exit_code is None else str(run.exit_code)
            lines.append(
                f"| {run.runtime} | {run.status} | {run.duration_ms} | {exit_code} | "
                f"{run.timed_out} | {run.output_truncated} |"
            )
    else:
        lines.append("| not started | blocked | 0 | - | false | false |")

    metrics = report.monitoring
    lines.extend([
        "",
        "## Monitoring",
        "",
        f"- Total duration: {metrics.total_duration_ms} ms",
        f"- Sandbox duration: {metrics.sandbox_duration_ms} ms",
        f"- Tool calls: {metrics.tool_calls}",
        f"- Filter interceptions: {metrics.interception_count}",
        f"- Findings: {metrics.finding_count}",
        f"- Warnings: {metrics.warning_count}",
        f"- Redactions: {metrics.redaction_count}",
        f"- Exception distribution: `{json.dumps(metrics.exception_distribution, sort_keys=True)}`",
        "",
        "## Operational Warnings",
        "",
    ])
    if report.operational_warnings:
        lines.extend(f"- {warning}" for warning in report.operational_warnings)
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def _render_finding(finding: Finding) -> list[str]:
    evidence = finding.evidence.replace("`", "'")
    return [
        f"### [{finding.severity.upper()}] {finding.title}",
        "",
        f"- Category: `{finding.category}`",
        f"- Location: `{finding.file}:{finding.line}`",
        f"- Confidence: `{finding.confidence:.2f}`",
        f"- Source: `{finding.source}`",
        f"- Evidence: `{evidence}`",
        f"- Recommendation: {finding.recommendation}",
        "",
    ]


def _table_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
