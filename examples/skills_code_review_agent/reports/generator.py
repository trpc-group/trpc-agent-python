# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report generator for the code review agent.

Generates JSON and Markdown format review reports from the analysis results.
The generation logic is shared with review_agent.py; this module provides
convenience wrappers and file I/O helpers.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from storage.models import FilterLog, Finding, MonitorSummary, ReviewTask, SandboxRun


def generate_json_report(
    task: ReviewTask,
    findings: list[Finding],
    warnings: list[Finding],
    needs_review: list[Finding],
    sandbox_runs: list[SandboxRun],
    filter_intercepts: list[FilterLog],
    monitor: Optional[MonitorSummary],
) -> str:
    """Generate a JSON format review report.

    Args:
        task: The review task.
        findings: High-confidence findings.
        warnings: Warning-level findings.
        needs_review: Findings needing human review.
        sandbox_runs: Sandbox execution records.
        filter_intercepts: Filter interception records.
        monitor: Monitoring summary.

    Returns:
        JSON string of the full report.
    """
    report: dict[str, Any] = {
        "task_id": task.id,
        "status": task.status.value,
        "input_type": task.input_type,
        "input_summary": json.loads(task.input_summary) if task.input_summary else {},
        "total_duration_ms": task.total_duration_ms,
        "finding_count": task.finding_count,
        "severity_distribution": json.loads(task.severity_distribution) if task.severity_distribution else {},
        "findings": [f.model_dump() for f in findings],
        "warnings": [f.model_dump() for f in warnings],
        "needs_human_review": [f.model_dump() for f in needs_review],
        "sandbox_runs": [s.model_dump() for s in sandbox_runs],
        "filter_intercepts": [i.model_dump() for i in filter_intercepts],
        "monitoring": monitor.model_dump() if monitor else {},
    }
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


def generate_markdown_report(
    task: ReviewTask,
    findings: list[Finding],
    warnings: list[Finding],
    needs_review: list[Finding],
    sandbox_runs: list[SandboxRun],
    filter_intercepts: list[FilterLog],
    monitor: Optional[MonitorSummary],
) -> str:
    """Generate a Markdown format review report.

    Args:
        task: The review task.
        findings: High-confidence findings.
        warnings: Warning-level findings.
        needs_review: Findings needing human review.
        sandbox_runs: Sandbox execution records.
        filter_intercepts: Filter interception records.
        monitor: Monitoring summary.

    Returns:
        Markdown string of the full report.
    """
    severity_dist = json.loads(task.severity_distribution) if task.severity_distribution else {}
    n_critical = severity_dist.get("critical", 0)
    n_warning = severity_dist.get("warning", 0)
    n_suggestion = severity_dist.get("suggestion", 0)

    lines = [
        "# 代码审查报告",
        "",
        f"**任务 ID**: {task.id}",
        f"**状态**: {task.status.value}",
        f"**耗时**: {task.total_duration_ms:.0f}ms",
        "",
        "## 摘要",
        "",
        "| 指标 | 数量 |",
        "|------|------|",
        f"| 🚨 Critical | {n_critical} |",
        f"| ⚠️ Warning | {n_warning} |",
        f"| 💡 Suggestion | {n_suggestion} |",
        f"| 待人工复核 | {len(needs_review)} |",
        f"| 沙箱执行 | {len(sandbox_runs)} |",
        f"| Filter 拦截 | {len(filter_intercepts)} |",
        "",
    ]

    if findings:
        lines.append("## 🚨 必须修复")
        lines.append("")
        for f in findings:
            lines.append(f"### {f.title}")
            lines.append("")
            lines.append(f"- **文件**: `{f.file_path}` L{f.line_number}")
            lines.append(f"- **类别**: {f.category.value}")
            lines.append(f"- **置信度**: {f.confidence.value}")
            lines.append(f"- **证据**: `{f.evidence}`")
            lines.append(f"- **建议**: {f.recommendation}")
            lines.append("")

    if warnings:
        lines.append("## ⚠️ 建议修复")
        lines.append("")
        for f in warnings:
            lines.append(f"### {f.title}")
            lines.append("")
            lines.append(f"- **文件**: `{f.file_path}` L{f.line_number}")
            lines.append(f"- **类别**: {f.category.value}")
            lines.append(f"- **证据**: `{f.evidence}`")
            lines.append(f"- **建议**: {f.recommendation}")
            lines.append("")

    if needs_review:
        lines.append("## 🔍 待人工复核")
        lines.append("")
        for f in needs_review:
            lines.append(f"- **{f.title}** (`{f.file_path}` L{f.line_number}) — {f.evidence}")
        lines.append("")

    if filter_intercepts:
        lines.append("## 🔒 Filter 拦截记录")
        lines.append("")
        lines.append("| 类型 | 动作 | 目标 | 原因 |")
        lines.append("|------|------|------|------|")
        for fi in filter_intercepts:
            lines.append(f"| {fi.filter_type.value} | {fi.action.value} | {fi.target or '-'} | {fi.reason or '-'} |")
        lines.append("")

    if sandbox_runs:
        lines.append("## ⚡ 沙箱执行摘要")
        lines.append("")
        lines.append("| 脚本 | 状态 | 耗时(ms) | 输出大小 |")
        lines.append("|------|------|---------|---------|")
        for s in sandbox_runs:
            lines.append(f"| {s.script_name} | {s.status.value} | {s.duration_ms:.0f} | {s.output_size_bytes} bytes |")
        if any(s.error_message for s in sandbox_runs):
            lines.append("")
            lines.append("**错误详情**:")
            for s in sandbox_runs:
                if s.error_message:
                    lines.append(f"- `{s.script_name}`: {s.error_message}")
        lines.append("")

    if monitor:
        lines.append("## 📊 监控指标")
        lines.append("")
        lines.append(f"- 总耗时: {monitor.total_duration_ms:.0f}ms")
        lines.append(f"- 沙箱耗时: {monitor.sandbox_duration_ms:.0f}ms")
        lines.append(f"- 工具调用次数: {monitor.tool_call_count}")
        lines.append(f"- 拦截次数: {monitor.intercept_count}")

    return "\n".join(lines)


def write_reports(
    output_dir: str,
    task: ReviewTask,
    findings: list[Finding],
    warnings: list[Finding],
    needs_review: list[Finding],
    sandbox_runs: list[SandboxRun],
    filter_intercepts: list[FilterLog],
    monitor: Optional[MonitorSummary],
) -> tuple[str, str]:
    """Generate and write both JSON and Markdown reports to disk.

    Args:
        output_dir: Directory to write reports to.
        task: The review task.
        findings: High-confidence findings.
        warnings: Warning-level findings.
        needs_review: Findings needing human review.
        sandbox_runs: Sandbox execution records.
        filter_intercepts: Filter interception records.
        monitor: Monitoring summary.

    Returns:
        Tuple of (json_path, md_path).
    """
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, "review_report.json")
    md_path = os.path.join(output_dir, "review_report.md")

    json_content = generate_json_report(
        task, findings, warnings, needs_review,
        sandbox_runs, filter_intercepts, monitor,
    )
    md_content = generate_markdown_report(
        task, findings, warnings, needs_review,
        sandbox_runs, filter_intercepts, monitor,
    )

    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_content)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return json_path, md_path