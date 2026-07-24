# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report assembly and rendering (review_report.json + review_report.md).

The markdown report carries every section acceptance criterion 8 demands:
findings 摘要 / 严重级别统计 / 人工复核项 / Filter 拦截摘要 / 监控指标 /
沙箱执行摘要 / 可执行修复建议.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List

from .findings import Finding
from .findings import severity_distribution
from .metrics import ReviewMetrics

_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


def build_report(task_id: str,
                 input_type: str,
                 input_ref: str,
                 status: str,
                 summary: str,
                 findings: List[Finding],
                 needs_human_review: List[Finding],
                 filter_events: List[Dict[str, Any]],
                 sandbox_runs: List[Dict[str, Any]],
                 metrics: ReviewMetrics,
                 diff_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the full report document (also persisted to cr_report.report)."""
    blocked_events = [event for event in filter_events if event.get("action") != "allow"]
    recommendations = [{
        "file": finding.file,
        "line": finding.line,
        "severity": finding.severity,
        "title": finding.title,
        "recommendation": finding.recommendation,
    } for finding in sorted(findings, key=lambda f: -f.severity_rank)]

    return {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {"type": input_type, "ref": input_ref, "diff_summary": diff_summary},
        "status": status,
        "summary": summary,
        "findings": [finding.to_dict() for finding in findings],
        "needs_human_review": [finding.to_dict() for finding in needs_human_review],
        "severity_stats": severity_distribution(findings),
        "filter_summary": {
            "total_decisions": len(filter_events),
            "blocked": len(blocked_events),
            "events": filter_events,
        },
        "sandbox_summary": {
            "total_runs": len(sandbox_runs),
            "runs": sandbox_runs,
        },
        "metrics": metrics.to_dict(),
        "recommendations": recommendations,
    }


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _findings_table(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return ["(none 无)"]
    lines = [
        "| Severity | Category | File:Line | Title | Confidence | Source |",
        "|---|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(f"| {item['severity']} | {item['category']} "
                     f"| `{_md_escape(item['file'])}:{item['line']}` "
                     f"| {_md_escape(item['title'])} | {item['confidence']:.2f} "
                     f"| {item['source']} |")
    return lines


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the bilingual markdown report."""
    lines: List[str] = []
    lines.append("# Code Review Report 代码评审报告")
    lines.append("")
    lines.append(f"- Task ID: `{report['task_id']}`")
    lines.append(f"- Status 状态: **{report['status']}**")
    lines.append(f"- Created 生成时间: {report['created_at']}")
    input_info = report.get("input", {})
    lines.append(f"- Input 输入: {input_info.get('type')} — `{_md_escape(str(input_info.get('ref')))}`")
    diff_summary = input_info.get("diff_summary") or {}
    if diff_summary:
        lines.append(f"- Diff: {diff_summary.get('file_count', 0)} file(s), "
                     f"{diff_summary.get('hunk_count', 0)} hunk(s), "
                     f"+{diff_summary.get('added_line_count', 0)} "
                     f"/ -{diff_summary.get('removed_line_count', 0)}")
    lines.append("")

    lines.append("## Findings 摘要")
    lines.append("")
    if report.get("summary"):
        lines.append(report["summary"])
        lines.append("")
    lines.extend(_findings_table(report.get("findings", [])))
    lines.append("")
    for item in report.get("findings", []):
        lines.append(f"### [{item['severity'].upper()}] {item['title']} — "
                     f"`{item['file']}:{item['line']}`")
        lines.append("")
        lines.append(f"- Rule 规则: `{item.get('rule_id', '')}` (category: {item['category']}, "
                     f"confidence: {item['confidence']:.2f}, source: {item['source']})")
        lines.append(f"- Evidence 证据: `{_md_escape(item['evidence'])}`")
        lines.append(f"- Recommendation 修复建议: {item['recommendation']}")
        lines.append("")

    lines.append("## 严重级别统计 Severity Stats")
    lines.append("")
    stats = report.get("severity_stats", {})
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for severity in _SEVERITY_ORDER:
        lines.append(f"| {severity} | {stats.get(severity, 0)} |")
    lines.append("")

    lines.append("## 人工复核项 Needs Human Review")
    lines.append("")
    lines.append("低置信度结果不混入高置信 findings，由人工确认。 "
                 "Low-confidence results are kept out of the findings list for human triage.")
    lines.append("")
    lines.extend(_findings_table(report.get("needs_human_review", [])))
    lines.append("")

    lines.append("## Filter 拦截摘要 Filter Blocks")
    lines.append("")
    filter_summary = report.get("filter_summary", {})
    lines.append(f"Decisions 决策总数: {filter_summary.get('total_decisions', 0)}; "
                 f"Blocked 拦截: {filter_summary.get('blocked', 0)}")
    blocked = [event for event in filter_summary.get("events", []) if event.get("action") != "allow"]
    if blocked:
        lines.append("")
        lines.append("| Stage | Target | Action | Rule | Reasons |")
        lines.append("|---|---|---|---|---|")
        for event in blocked:
            reasons = "; ".join(event.get("reasons") or [])
            lines.append(f"| {event.get('stage')} | `{_md_escape(str(event.get('target')))}` "
                         f"| **{event.get('action')}** | {event.get('rule')} "
                         f"| {_md_escape(reasons)} |")
    lines.append("")

    lines.append("## 监控指标 Metrics")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.get("metrics", {}), indent=2, ensure_ascii=False))
    lines.append("```")
    lines.append("")

    lines.append("## 沙箱执行摘要 Sandbox Runs")
    lines.append("")
    sandbox_summary = report.get("sandbox_summary", {})
    runs = sandbox_summary.get("runs", [])
    if runs:
        lines.append("| # | Kind | Runtime | Status | Exit | Timed out | Duration (ms) | Error |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for run in runs:
            lines.append(f"| {run.get('run_index', 0)} | {run.get('kind')} "
                         f"| {run.get('runtime_kind')} | {run.get('status')} "
                         f"| {run.get('exit_code', '')} | {run.get('timed_out', False)} "
                         f"| {run.get('duration_ms', 0):.0f} | {run.get('error_type', '')} |")
    else:
        lines.append("(no sandbox runs 无沙箱执行)")
    lines.append("")

    lines.append("## 修复建议 Recommendations")
    lines.append("")
    recommendations = report.get("recommendations", [])
    if recommendations:
        for index, rec in enumerate(recommendations, 1):
            lines.append(f"{index}. **[{rec['severity']}]** `{rec['file']}:{rec['line']}` — "
                         f"{rec['recommendation']}")
    else:
        lines.append("No actionable fixes required. 无需修复。")
    lines.append("")
    return "\n".join(lines)


def write_reports(report: Dict[str, Any], out_dir: str) -> Dict[str, str]:
    """Write review_report.json and review_report.md; returns their paths."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "review_report.json")
    md_path = os.path.join(out_dir, "review_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))
    return {"json": json_path, "markdown": md_path}
