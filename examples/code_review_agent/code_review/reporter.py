"""JSON and Markdown report generation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import Finding, ReviewRun, Severity


def write_reports(review_run: ReviewRun, output_directory: str | Path) -> tuple[Path, Path]:
    """Write canonical JSON and human-readable Markdown reports."""
    output_path = Path(output_directory).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "review.json"
    markdown_path = output_path / "review.md"
    json_path.write_text(
        json.dumps(review_run.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(review_run), encoding="utf-8")
    return json_path, markdown_path


def render_markdown(review_run: ReviewRun) -> str:
    """Render a compact report suitable for a check summary."""
    counts = Counter(finding.severity for finding in review_run.output.findings)
    publishable = sum(finding.publishable for finding in review_run.output.findings)
    lines = [
        "# Code Review Report",
        "",
        f"- Run: `{review_run.id}`",
        f"- Status: `{review_run.status.value}`",
        f"- Repository: `{review_run.repository_path}`",
        f"- Base: `{review_run.base_revision}`",
        f"- Effective base: `{review_run.effective_base_revision}`",
        f"- Head: `{review_run.head_revision}`",
        f"- Changed files: {len(review_run.changed_files)}",
        f"- Findings: {len(review_run.output.findings)}",
        f"- Line-comment eligible: {publishable}",
        "",
        "## Summary",
        "",
        review_run.output.summary or "No summary was produced.",
        "",
        "## Severity",
        "",
    ]
    for severity in Severity:
        lines.append(f"- {severity.value}: {counts[severity]}")

    lines.extend(["", "## Static Analysis", ""])
    if not review_run.static_analysis_requested:
        lines.extend(["Static analysis was not requested.", ""])
    elif not review_run.analyzer_executions:
        lines.extend(["Static analysis was requested, but no eligible files required a tool run.", ""])
    else:
        lines.extend([
            "| Tool | Runtime | Status | Exit | Findings | Duration |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ])
        for execution in review_run.analyzer_executions:
            exit_code = "" if execution.exit_code is None else str(execution.exit_code)
            lines.append(f"| {execution.tool} | {execution.runtime} | {execution.status.value} | "
                         f"{exit_code} | {execution.findings_count} | {execution.duration_seconds:.2f}s |")
        lines.append("")

    lines.extend(["", "## Findings", ""])
    if not review_run.output.findings:
        lines.extend(["No actionable findings.", ""])
    else:
        for index, finding in enumerate(review_run.output.findings, start=1):
            lines.extend(_render_finding(index, finding))

    if review_run.diagnostics:
        lines.extend(["## Diagnostics", ""])
        lines.extend(f"- {diagnostic}" for diagnostic in review_run.diagnostics)
        lines.append("")
    return "\n".join(lines)


def _render_finding(index: int, finding: Finding) -> list[str]:
    location = finding.file_path
    if finding.start_line is not None:
        location += f":{finding.start_line}"
        if finding.end_line != finding.start_line:
            location += f"-{finding.end_line}"
    publishable = "yes" if finding.publishable else "no"
    lines = [
        f"### {index}. [{finding.severity.value.upper()}] {finding.title}",
        "",
        f"- Rule: `{finding.rule_id}`",
        f"- Category: `{finding.category}`",
        f"- Location: `{location}`",
        f"- Confidence: {finding.confidence:.2f}",
        f"- Line-comment eligible: {publishable}",
        f"- Source: `{finding.source}`",
        "",
        finding.description,
        "",
    ]
    if finding.suggestion:
        lines.extend(["**Suggestion:**", "", finding.suggestion, ""])
    return lines
