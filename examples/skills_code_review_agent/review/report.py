# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report assembly and rendering."""
import json
import os
from datetime import datetime

from .findings import severity_distribution
from .redaction import redact_text

_BLOCKING = {"critical", "high"}


def _conclusion(reported, filter_events):
    if any(f.severity in _BLOCKING for f in reported):
        return "blocked"
    intercepted = any(e.decision != "allow" for e in filter_events)
    if reported or intercepted:
        return "needs_attention"
    return "pass"


def build_report(task_id, input_ref, runtime, dry_run, diff_summary, reported,
                 needs_review, deduped_count, filter_events, sandbox_outcomes,
                 metrics, llm_summary, warnings) -> dict:
    """Assemble the full report dict (issue acceptance criterion #8)."""
    return {
        "task_id": task_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input": {"ref": input_ref, "runtime": runtime, "dry_run": dry_run,
                  "diff_summary": diff_summary},
        "conclusion": _conclusion(reported, filter_events),
        "summary": {
            "total_findings": len(reported),
            "by_severity": severity_distribution(reported),
            "needs_human_review_count": len(needs_review),
            "deduplicated": deduped_count,
            "intercepts": sum(1 for e in filter_events if e.decision != "allow"),
        },
        "findings": [f.model_dump() for f in reported],
        "needs_human_review": [f.model_dump() for f in needs_review],
        "filter_intercepts": [
            {"target": e.target, "decision": e.decision, "rule": e.rule, "reason": e.reason}
            for e in filter_events if e.decision != "allow"],
        "sandbox_runs": [
            {"script": o.script, "status": o.status, "exit_code": o.exit_code,
             "duration_ms": o.duration_ms, "timed_out": o.timed_out,
             "error_type": o.error_type} for o in sandbox_outcomes],
        "metrics": metrics,
        "llm_summary": llm_summary,
        "warnings": list(warnings),
    }


def _md_findings_table(findings):
    if not findings:
        return "_none_\n"
    lines = ["| Severity | Category | File | Line | Title | Confidence | Source |",
             "|---|---|---|---|---|---|---|"]
    for f in findings:
        lines.append(f"| {f['severity']} | {f['category']} | {f['file']} | {f['line']} "
                     f"| {f['title']} | {f['confidence']} | {f['source']} |")
    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    """Render the human-readable markdown report."""
    s = report["summary"]
    parts = [
        "# Code Review Report",
        f"- Task: `{report['task_id']}`",
        f"- Conclusion: **{report['conclusion']}**",
        f"- Input: {report['input']['ref']} (runtime={report['input']['runtime']}, "
        f"dry_run={report['input']['dry_run']})",
        f"- Findings: {s['total_findings']} (by severity: {s['by_severity']}), "
        f"needs human review: {s['needs_human_review_count']}, "
        f"deduplicated: {s['deduplicated']}, intercepts: {s['intercepts']}",
        "",
        "## Findings",
        _md_findings_table(report["findings"]),
        "### Recommendations",
        "\n".join(f"- `{f['file']}:{f['line']}` {f['recommendation']}"
                  for f in report["findings"]) or "_none_",
        "",
        "## Needs Human Review",
        _md_findings_table(report["needs_human_review"]),
        "## Filter Intercepts",
        "\n".join(f"- [{e['decision']}] `{e['target']}` ({e['rule']}): {e['reason']}"
                  for e in report["filter_intercepts"]) or "_none_",
        "",
        "## Sandbox Runs",
        "\n".join(f"- `{o['script']}`: {o['status']} (exit={o['exit_code']}, "
                  f"{o['duration_ms']}ms, timed_out={o['timed_out']})"
                  for o in report["sandbox_runs"]) or "_none_",
        "",
        "## Metrics",
        "```json",
        json.dumps(report["metrics"], indent=2),
        "```",
        "",
        f"## LLM Summary\n{report['llm_summary'] or '_none_'}",
        "",
        "## Warnings",
        "\n".join(f"- {w}" for w in report["warnings"]) or "_none_",
    ]
    return "\n".join(parts) + "\n"


def write_reports(report: dict, output_dir: str):
    """Write review_report.json / review_report.md (final redaction pass)."""
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "review_report.json")
    md_path = os.path.join(output_dir, "review_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        fh.write(redact_text(json.dumps(report, indent=2, ensure_ascii=False, default=str)))
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(redact_text(render_markdown(report)))
    return json_path, md_path
