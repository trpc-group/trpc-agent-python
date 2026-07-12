"""Human and machine report writers."""

from __future__ import annotations

import json
from pathlib import Path

from models import ReviewReport


def write_reports(report: ReviewReport, output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "review_report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "review_report.md").write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: ReviewReport) -> str:
    severity = report.monitoring.get("severity_distribution", {})
    lines = [
        "# Code Review Report", "", f"**Task:** `{report.task_id}`",
        f"**Conclusion:** `{report.conclusion}`", "",
        "## Summary", "",
        f"- Findings: {len(report.findings)}",
        f"- Human-review warnings: {len(report.warnings)}",
        f"- Filter blocks: {len(report.filter_blocks)}",
        f"- Sandbox runs: {len(report.sandbox_runs)}",
        f"- Severity distribution: `{json.dumps(severity, sort_keys=True)}`", "",
        "## Findings", "",
    ]
    if not report.findings:
        lines.append("No high-confidence findings.")
    for finding in report.findings:
        lines.extend([
            f"### [{finding.severity.upper()}] {finding.title}", "",
            f"`{finding.file}:{finding.line}` · `{finding.category}` · confidence {finding.confidence:.2f}", "",
            f"Evidence: `{finding.evidence}`", "",
            f"Recommendation: {finding.recommendation}", "",
        ])
    lines.extend(["## Needs human review", ""])
    if not report.warnings:
        lines.append("None.")
    for warning in report.warnings:
        lines.append(f"- `{warning.file}:{warning.line}` {warning.title}: {warning.recommendation}")
    lines.extend(["", "## Filter and sandbox", ""])
    if not report.sandbox_runs:
        lines.append("No sandbox checks requested.")
    for run in report.sandbox_runs:
        lines.append(
            f"- `{run.status}` `{run.command}` in {run.duration_ms:.2f} ms"
            + (f" — {run.filter_reason}" if run.filter_reason else "")
        )
    lines.extend(["", "## Monitoring", "", "```json",
                  json.dumps(report.monitoring, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)
