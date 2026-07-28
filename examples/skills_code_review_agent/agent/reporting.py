"""Atomic JSON and Markdown report rendering."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import ReportPaths, ReviewReport


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def render_markdown(report: ReviewReport) -> str:
    severity: dict[str, int] = {}
    for finding in report.findings:
        severity[finding.severity] = severity.get(finding.severity, 0) + 1
    lines = [
        "# Code Review Report",
        "",
        f"- Task: `{report.task_id}`",
        f"- Status: **{report.status}**",
        f"- Dry run: **{'yes' if report.dry_run else 'no'}**",
        f"- Input: {report.input_summary}",
        f"- Rule set digest: `{report.rule_set_digest}`",
        f"- Conclusion: {report.conclusion}",
        "",
        "## Severity summary",
        "",
    ]
    lines.extend([f"- {name}: {count}" for name, count in sorted(severity.items())] or ["- No accepted findings"])
    lines.extend(["", "## Findings", ""])
    for item in report.findings:
        lines.extend(
            [
                f"### [{item.severity.upper()}] {item.title}",
                "",
                f"`{item.file}:{item.line}` · {item.category} · confidence {item.confidence:.2f}",
                "",
                item.evidence,
                "",
                f"Recommendation: {item.recommendation}",
                "",
            ]
        )
    if not report.findings:
        lines.extend(["No accepted findings.", ""])
    lines.extend(["## Human review", ""])
    lines.extend(
        [f"- `{item.file}:{item.line}` {item.title} ({item.confidence:.2f})" for item in report.needs_human_review]
        or ["- None"]
    )
    lines.extend(["", "## Execution and policy", ""])
    lines.append(f"- Sandbox checks: {len(report.sandbox_runs)}")
    lines.append(f"- Blocked decisions: {sum(item.decision != 'allow' for item in report.filter_decisions)}")
    for run in report.sandbox_runs:
        lines.append(
            f"- `{run.task_type}`: {run.status}, exit={run.exit_code}, "
            f"duration={run.duration_ms}ms"
        )
    lines.extend(["", "## Monitoring", ""])
    lines.append(f"- Total duration: {report.metrics.get('total_duration_ms', 0)}ms")
    lines.append(f"- Tool calls: {report.metrics.get('tool_calls', 0)}")
    lines.append(
        f"- Filter blocks: {report.metrics.get('blocked_executions', 0)}"
    )
    lines.append(
        f"- Errors: {json.dumps(report.metrics.get('errors', {}), ensure_ascii=False)}"
    )
    if report.warnings:
        lines.extend(["", "## Warnings", "", *[f"- {warning}" for warning in report.warnings]])
    return "\n".join(lines) + "\n"


def write_reports(report: ReviewReport, output_dir: str | Path, *, max_report_bytes: int = 5_000_000) -> ReportPaths:
    output = Path(output_dir) / report.task_id
    json_content = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    markdown_content = render_markdown(report)
    if max(len(json_content.encode()), len(markdown_content.encode())) > max_report_bytes:
        raise ValueError("generated report exceeds the configured size limit")
    filter_events_content = json.dumps(
        [
            {
                "task_id": item.task_id or report.task_id,
                "command_digest": item.command_digest,
                "decision": item.decision.value,
                "risk_level": item.risk_level,
                "reason": item.reason,
                "matched_rule": item.matched_rule or item.reason_code,
                "created_at": item.created_at.isoformat(),
            }
            for item in report.filter_decisions
        ],
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    if len(filter_events_content.encode()) > max_report_bytes:
        raise ValueError("filter audit output exceeds the configured size limit")
    json_path, markdown_path = output / "review_report.json", output / "review_report.md"
    filter_events_path = output / "filter_events.json"
    _atomic_write(json_path, json_content)
    _atomic_write(markdown_path, markdown_content)
    _atomic_write(filter_events_path, filter_events_content)
    return ReportPaths(
        json_path=json_path,
        markdown_path=markdown_path,
        filter_events_path=filter_events_path,
    )
