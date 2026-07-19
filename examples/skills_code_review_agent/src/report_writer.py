"""Report generation helpers for JSON and Markdown review outputs."""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from .review_types import ReviewFinding, ReviewReport


def build_report_payload(report: ReviewReport) -> dict[str, object]:
    """Convert a structured report into a JSON-safe payload."""

    return json.loads(json.dumps(asdict(report), default=_json_default))


def render_markdown_report(report: ReviewReport) -> str:
    """Render the markdown report required by the issue."""

    lines: list[str] = [
        "# Review Report",
        "",
        f"- Task ID: `{report.task_id}`",
        f"- Final Verdict: `{report.conclusion.value}`",
        "",
        "## Summary",
        "",
        report.summary or "No summary available.",
        "",
        "## Severity Stats",
        "",
    ]

    if report.severity_counts:
        for severity, count in sorted(report.severity_counts.items()):
            lines.append(f"- `{severity}`: {count}")
    else:
        lines.append("- No final findings.")

    lines.extend(
        [
            "",
            "## Findings",
            "",
        ]
    )
    if report.findings:
        lines.extend(_render_finding_list(report.findings))
    else:
        lines.append("- No final findings.")

    lines.extend(
        [
            "",
            "## Human Review Items",
            "",
        ]
    )
    if report.needs_human_review:
        lines.extend(_render_finding_list(report.needs_human_review))
    else:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "## Warnings",
            "",
        ]
    )
    if report.warnings:
        lines.extend(_render_finding_list(report.warnings))
    else:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "## Filter Summary",
            "",
        ]
    )
    if report.filter_decisions:
        for decision in report.filter_decisions:
            lines.append(
                f"- `{decision.decision.value}` on `{decision.target}`: {decision.reason}"
            )
    else:
        lines.append("- No filter interceptions.")

    lines.extend(
        [
            "",
            "## Sandbox Summary",
            "",
        ]
    )
    if report.sandbox_runs:
        for sandbox_run in report.sandbox_runs:
            lines.append(
                f"- `{sandbox_run.name}` status=`{sandbox_run.status.value}` "
                f"duration={sandbox_run.duration_ms}ms exit_code={sandbox_run.exit_code}"
            )
    else:
        lines.append("- No sandbox executions.")

    lines.extend(
        [
            "",
            "## Monitoring",
            "",
        ]
    )
    for key, value in sorted(report.monitoring_summary.items()):
        lines.append(f"- `{key}`: {value}")

    lines.extend(
        [
            "",
            "## Actionable Recommendations",
            "",
        ]
    )
    recommendations = _collect_recommendations(report)
    if recommendations:
        for recommendation in recommendations:
            lines.append(f"- {recommendation}")
    else:
        lines.append("- No recommendations.")

    return "\n".join(lines) + "\n"


def write_report_files(
    report: ReviewReport,
    *,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """Write both JSON and Markdown reports to the output directory."""

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / "review_report.json"
    markdown_path = output_path / "review_report.md"

    json_path.write_text(
        json.dumps(build_report_payload(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, markdown_path


def _render_finding_list(findings: list[ReviewFinding]) -> list[str]:
    """Render a flat markdown list of findings."""

    lines: list[str] = []
    for finding in findings:
        location = (
            f"{finding.file}:{finding.line}"
            if finding.line is not None
            else finding.file
        )
        lines.append(
            f"- [`{finding.severity.value}`] `{finding.category.value}` at `{location}`: {finding.title}"
        )
        lines.append(f"  Evidence: `{finding.evidence}`")
        lines.append(f"  Recommendation: {finding.recommendation}")
    return lines


def _collect_recommendations(report: ReviewReport) -> list[str]:
    """Collect unique actionable recommendations from all report buckets."""

    seen: set[str] = set()
    ordered: list[str] = []
    for finding in report.findings + report.needs_human_review + report.warnings:
        if finding.recommendation not in seen:
            seen.add(finding.recommendation)
            ordered.append(finding.recommendation)
    return ordered


def _json_default(value: object) -> object:
    """Serialize enums and paths for report payloads."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
