# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Build and render the review report (issue #92, requirement: report with 7 sections).

The 7 sections (findings summary, severity stats, human-review items, Filter-block summary,
monitoring metrics, sandbox summary, actionable findings) all exist from day 1; sections not yet
populated by a given slice render empty so the schema never churns across PRs.
"""
from __future__ import annotations

import json

from .redaction import redact_report
from .types import Finding, ReviewReport, SandboxRunResult

_SEVERITY_ORDER = ["critical", "high", "medium", "low"]


def build_report(
    task_id: str,
    findings: list[Finding],
    *,
    sandbox_runs: list[SandboxRunResult] | None = None,
    filter_blocks: list[dict] | None = None,
    monitoring: dict | None = None,
) -> ReviewReport:
    sandbox_runs = sandbox_runs or []
    filter_blocks = filter_blocks or []
    active = [f for f in findings if f.status == "active"]
    warnings = [f for f in findings if f.status == "warning"]
    human = [f for f in findings if f.status == "needs_human_review"]

    severity_stats = {s: sum(1 for f in active if f.severity == s) for s in _SEVERITY_ORDER}
    category_stats: dict[str, int] = {}
    for f in active:
        category_stats[f.category] = category_stats.get(f.category, 0) + 1

    report = ReviewReport(
        task_id=task_id,
        findings_summary={
            "total": len(active),
            "warnings": len(warnings),
            "needs_human_review": len(human),
            "by_category": category_stats,
        },
        severity_stats=severity_stats,
        human_review=human + warnings,
        filter_blocks=filter_blocks,
        monitoring=monitoring or {},
        sandbox_summary=sandbox_runs,
        findings=active,
    )
    return redact_report(report)  # defense in depth; findings are already redacted on entry


def render_json(report: ReviewReport) -> str:
    return json.dumps(report.model_dump(), indent=2, ensure_ascii=False)


def _fmt_finding(f: Finding) -> str:
    loc = f"{f.file}:{f.line}" if f.line is not None else f.file
    return (f"- **[{f.severity}] {f.title}** (`{loc}`, {f.category}, conf={f.confidence:.2f}, "
            f"{f.source})\n  - {f.evidence}\n  - _Fix:_ {f.recommendation}")


def render_md(report: ReviewReport) -> str:
    s = report.severity_stats
    lines = [
        f"# Code Review Report — `{report.task_id}`",
        "",
        "## 1. Findings summary",
        f"- Active findings: **{report.findings_summary.get('total', 0)}**",
        f"- Warnings: {report.findings_summary.get('warnings', 0)}",
        f"- Needs human review: {report.findings_summary.get('needs_human_review', 0)}",
        "",
        "## 2. Severity statistics",
        f"- critical: {s.get('critical', 0)} · high: {s.get('high', 0)} · "
        f"medium: {s.get('medium', 0)} · low: {s.get('low', 0)}",
        "",
        "## 3. Needs human review",
        *([_fmt_finding(f) for f in report.human_review] or ["_none_"]),
        "",
        "## 4. Filter interception summary",
        *([f"- {b}" for b in report.filter_blocks] or ["_none_"]),
        "",
        "## 5. Monitoring metrics",
        *([f"- {k}: {v}" for k, v in report.monitoring.items()] or ["_none_"]),
        "",
        "## 6. Sandbox execution summary",
        *([
            f"- `{r.script}` exit={r.exit_code} dur={r.duration_sec:.2f}s "
            f"timed_out={r.timed_out} blocked={r.blocked}" for r in report.sandbox_summary
        ] or ["_none_"]),
        "",
        "## 7. Findings & fixes",
        *([_fmt_finding(f) for f in report.findings] or ["_no active findings_"]),
        "",
    ]
    return "\n".join(lines)
