# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report rendering: review_report.json + review_report.md.

Both formats carry the seven acceptance-required elements:
findings summary, severity statistics, human-review items, filter-block
summary, monitoring metrics, sandbox execution summary, and actionable fix
suggestions.  The JSON is the machine contract (consumed by eval.py and
tests); the Markdown is the same data for humans.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .findings import TriageResult
from .store import Metrics, ReviewTask, SandboxRun

SEVERITY_RANK = ("critical", "high", "medium", "low", "info")


def _finding_dict(row) -> dict:
    return {
        "rule_id": row.rule_id,
        "category": row.category,
        "severity": row.severity,
        "confidence": row.confidence,
        "source": row.source,
        "file": row.file,
        "line": row.line,
        "title": row.title,
        "evidence": row.evidence,
        "recommendation": row.recommendation,
        "fix": row.fix_json,
        "status": row.status,
    }


@dataclass
class ReportInputs:
    task: ReviewTask
    triage: TriageResult
    filter_events: list[dict]
    sandbox_runs: list[SandboxRun]
    metrics_row: Metrics
    notes: list[str]


def build_report_payload(inputs: ReportInputs) -> dict:
    """Assemble the canonical JSON report structure."""
    triage = inputs.triage
    severity_stats = {sev: 0 for sev in SEVERITY_RANK}
    for row in triage.reported:
        severity_stats[row.severity] = severity_stats.get(row.severity, 0) + 1

    blocked = [event for event in inputs.filter_events if event["decision"] != "allow"]
    fixes = [{
        "file": row.file,
        "line": row.line,
        "rule_id": row.rule_id,
        "before": (row.fix_json or {}).get("before"),
        "after": (row.fix_json or {}).get("after"),
    } for row in triage.reported if row.fix_json and (row.fix_json or {}).get("after")]

    metrics = inputs.metrics_row
    return {
        "version":
        1,
        "task": {
            "id": inputs.task.id,
            "status": inputs.task.status,
            "input_type": inputs.task.input_type,
            "input_ref": inputs.task.input_ref,
            "mode": inputs.task.mode,
            "runtime": inputs.task.runtime,
            "dry_run": inputs.task.dry_run,
            "diff_digest": inputs.task.diff_digest,
            "created_at": inputs.task.created_at.isoformat() if inputs.task.created_at else None,
            "error": inputs.task.error or "",
        },
        "summary": {
            "finding_count": len(triage.reported),
            "warning_count": len(triage.warnings),
            "needs_human_review_count": len(triage.needs_human),
            "suppressed_duplicates": len(triage.suppressed),
            "severity_stats": severity_stats,
            "notes": inputs.notes,
        },
        "findings": [_finding_dict(row) for row in triage.reported],
        "warnings": [_finding_dict(row) for row in triage.warnings],
        "needs_human_review": [_finding_dict(row) for row in triage.needs_human],
        "filter_summary": {
            "total_decisions": len(inputs.filter_events),
            "blocked": len(blocked),
            "events": inputs.filter_events,
        },
        "sandbox_summary": [{
            "tool": run.tool,
            "command": run.command,
            "runtime": run.runtime,
            "status": run.status,
            "exit_code": run.exit_code,
            "duration_ms": run.duration_ms,
            "timed_out": run.timed_out,
            "truncated": run.truncated,
            "stdout_digest": run.stdout_digest,
            "stderr_digest": run.stderr_digest,
        } for run in inputs.sandbox_runs],
        "metrics": {
            "total_ms": metrics.total_ms,
            "sandbox_ms": metrics.sandbox_ms,
            "tool_calls": metrics.tool_calls,
            "filter_blocks": metrics.filter_blocks,
            "finding_count": metrics.finding_count,
            "severity_dist": metrics.severity_dist_json,
            "error_dist": metrics.error_dist_json,
            "token_usage": metrics.token_usage_json,
            "phase_timings": metrics.phase_timings_json,
        },
        "fix_suggestions":
        fixes,
    }


def render_markdown(payload: dict) -> str:
    """Human-readable twin of the JSON report."""
    task = payload["task"]
    summary = payload["summary"]
    lines: list[str] = []
    lines.append("# Code Review Report")
    lines.append("")
    lines.append(f"- task: `{task['id']}` | status: **{task['status']}** | mode: {task['mode']} "
                 f"| runtime: {task['runtime']} | dry_run: {task['dry_run']}")
    lines.append(f"- input: {task['input_type']} `{task['input_ref']}` | diff sha256/16: `{task['diff_digest']}`")
    if task.get("error"):
        lines.append(f"- task error: `{task['error']}`")
    lines.append("")

    lines.append("## Findings summary")
    stats = summary["severity_stats"]
    lines.append("")
    lines.append("| severity | count |")
    lines.append("|---|---|")
    for sev in SEVERITY_RANK:
        lines.append(f"| {sev} | {stats.get(sev, 0)} |")
    lines.append("")
    lines.append(f"{summary['finding_count']} finding(s), {summary['warning_count']} warning(s), "
                 f"{summary['needs_human_review_count']} for human review, "
                 f"{summary['suppressed_duplicates']} duplicate(s) suppressed.")
    for note in summary["notes"]:
        lines.append(f"- note: {note}")
    lines.append("")

    def _emit_rows(rows: list[dict], heading: str) -> None:
        lines.append(f"## {heading}")
        lines.append("")
        if not rows:
            lines.append("(none)")
            lines.append("")
            return
        for row in rows:
            lines.append(f"### [{row['severity'].upper()}] {row['file']}:{row['line']} — {row['title']}")
            lines.append(f"- rule: `{row['rule_id']}` | category: {row['category']} | "
                         f"confidence: {row['confidence']} | source: {row['source']}")
            lines.append(f"- evidence: `{row['evidence']}`")
            lines.append(f"- recommendation: {row['recommendation']}")
            fix = row.get("fix") or {}
            if fix.get("after"):
                lines.append("- suggested fix:")
                lines.append("  ```")
                if fix.get("before"):
                    for text in str(fix["before"]).splitlines():
                        lines.append(f"  - {text}")
                for text in str(fix["after"]).splitlines():
                    lines.append(f"  + {text}")
                lines.append("  ```")
            lines.append("")

    _emit_rows(payload["findings"], "Findings")
    _emit_rows(payload["needs_human_review"], "Needs human review")
    _emit_rows(payload["warnings"], "Warnings (low confidence)")

    lines.append("## Filter decisions")
    lines.append("")
    filter_summary = payload["filter_summary"]
    lines.append(f"{filter_summary['total_decisions']} decision(s), {filter_summary['blocked']} blocked.")
    lines.append("")
    if filter_summary["events"]:
        lines.append("| tool | decision | rule | reason |")
        lines.append("|---|---|---|---|")
        for event in filter_summary["events"]:
            lines.append(f"| {event['tool_name']} | {event['decision']} | {event['rule']} "
                         f"| {event['reason'] or 'ok'} |")
        lines.append("")

    lines.append("## Sandbox executions")
    lines.append("")
    if payload["sandbox_summary"]:
        lines.append("| command | runtime | status | exit | duration_ms | timed_out |")
        lines.append("|---|---|---|---|---|---|")
        for run in payload["sandbox_summary"]:
            lines.append(f"| `{run['command']}` | {run['runtime']} | {run['status']} | {run['exit_code']} "
                         f"| {run['duration_ms']} | {run['timed_out']} |")
    else:
        lines.append("(no sandbox execution — blocked or failed before launch)")
    lines.append("")

    metrics = payload["metrics"]
    lines.append("## Metrics")
    lines.append("")
    lines.append(f"- total: {metrics['total_ms']} ms (sandbox: {metrics['sandbox_ms']} ms)")
    lines.append(f"- tool calls: {metrics['tool_calls']} | filter blocks: {metrics['filter_blocks']}")
    lines.append(f"- token usage: {json.dumps(metrics['token_usage'] or {})}")
    lines.append(f"- error distribution: {json.dumps(metrics['error_dist'] or {})}")
    lines.append(f"- phase timings (ms): {json.dumps(metrics['phase_timings'] or {})}")
    lines.append("")
    return "\n".join(lines)


def write_reports(payload: dict, out_dir: str) -> tuple[str, str]:
    """Write review_report.json / review_report.md; returns their paths."""
    import os

    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "review_report.json")
    md_path = os.path.join(out_dir, "review_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    markdown = render_markdown(payload)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    return json_path, md_path
