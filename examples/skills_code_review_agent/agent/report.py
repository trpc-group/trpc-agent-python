"""JSON and Markdown report generation."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .findings import Finding
from .redaction import redact_text


_RULES = [
    "static-rule:hardcoded-secret",
    "static-rule:sql-string-concat",
    "static-rule:http-timeout",
    "static-rule:broad-except",
    "static-rule:open-without-context-manager",
]


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _counts(items: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))


def build_report(
    *,
    diff_file: str,
    files: list[str],
    findings: list[Finding],
    dry_run: bool,
    filter_summary: dict[str, Any] | None = None,
    sandbox_summary: dict[str, Any] | None = None,
    telemetry_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finding_dicts = [_redact_value(finding.to_dict()) for finding in findings]
    severity_counts = _counts([str(item["severity"]) for item in finding_dicts])
    category_counts = _counts([str(item["category"]) for item in finding_dicts])
    filter_summary = filter_summary or {"decision": "allow", "reason": "not evaluated"}
    sandbox_summary = sandbox_summary or {
        "runner_name": "none",
        "timeout_seconds": 0,
        "status": "not_run",
        "started_at": "",
        "finished_at": "",
        "stdout_summary": "",
        "stderr_summary": "",
    }
    telemetry_summary = telemetry_summary or {
        "files_scanned": files,
        "total_findings": len(finding_dicts),
        "severity_counts": severity_counts,
        "category_counts": category_counts,
        "sandbox_status": sandbox_summary["status"],
        "filter_decision": filter_summary["decision"],
        "duration_ms": 0,
    }

    report = {
        "summary": {
            "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "dry_run": dry_run,
            "diff_file": diff_file,
            "files_scanned": files,
            "rules": _RULES,
            "total_findings": len(finding_dicts),
            "severity_counts": severity_counts,
            "category_counts": category_counts,
        },
        "filter": filter_summary,
        "sandbox": sandbox_summary,
        "telemetry": telemetry_summary,
        "findings": finding_dicts,
    }
    return _redact_value(report)


def _markdown_table_row(finding: dict[str, Any]) -> str:
    evidence = str(finding["evidence"]).replace("|", "\\|")
    recommendation = str(finding["recommendation"]).replace("|", "\\|")
    return (
        f"| {finding['severity']} | {finding['category']} | "
        f"{finding['file']}:{finding['line']} | {finding['title']} | "
        f"`{evidence}` | {recommendation} |"
    )


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    filter_summary = report.get("filter", {})
    sandbox_summary = report.get("sandbox", {})
    telemetry_summary = report.get("telemetry", {})
    lines = [
        "# Code Review Report",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Dry run: {summary['dry_run']}",
        f"- Diff file: `{summary['diff_file']}`",
        f"- Files scanned: {len(summary['files_scanned'])}",
        f"- Total findings: {summary['total_findings']}",
        f"- Filter decision: {filter_summary.get('decision', 'unknown')}",
        f"- Sandbox status: {sandbox_summary.get('status', 'unknown')}",
        "",
        "## Severity Counts",
        "",
    ]
    if summary["severity_counts"]:
        for severity, count in summary["severity_counts"].items():
            lines.append(f"- {severity}: {count}")
    else:
        lines.append("- none: 0")

    lines.extend([
        "",
        "## Filter Summary",
        "",
        f"- Decision: {filter_summary.get('decision', 'unknown')}",
        f"- Reason: {filter_summary.get('reason', '')}",
        "",
        "## Sandbox Summary",
        "",
        f"- Runner: {sandbox_summary.get('runner_name', 'unknown')}",
        f"- Timeout seconds: {sandbox_summary.get('timeout_seconds', 0)}",
        f"- Status: {sandbox_summary.get('status', 'unknown')}",
        f"- Started: {sandbox_summary.get('started_at', '')}",
        f"- Finished: {sandbox_summary.get('finished_at', '')}",
        f"- Stdout summary: {sandbox_summary.get('stdout_summary', '')}",
        f"- Stderr summary: {sandbox_summary.get('stderr_summary', '')}",
        "",
        "## Telemetry Summary",
        "",
        f"- Files scanned: {len(telemetry_summary.get('files_scanned', []))}",
        f"- Total findings: {telemetry_summary.get('total_findings', 0)}",
        f"- Sandbox status: {telemetry_summary.get('sandbox_status', 'unknown')}",
        f"- Filter decision: {telemetry_summary.get('filter_decision', 'unknown')}",
        f"- Duration ms: {telemetry_summary.get('duration_ms', 0)}",
    ])

    lines.extend(["", "## Findings", ""])
    findings = report["findings"]
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines) + "\n"

    lines.append(
        "| Severity | Category | Location | Title | Evidence | Recommendation |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for finding in findings:
        lines.append(_markdown_table_row(finding))
    return "\n".join(lines) + "\n"


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "review_report.json"
    md_path = output_dir / "review_report.md"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
