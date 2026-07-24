"""Review report rendering."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .models import Finding
from .models import ReviewMetrics
from .models import SandboxRun
from .redaction import redact_obj


def split_findings(findings: list[Finding]) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Split findings into confident findings, warnings and manual-review items."""
    confident: list[Finding] = []
    warnings: list[Finding] = []
    needs_human_review: list[Finding] = []
    for finding in findings:
        if finding.disposition == "needs_human_review" or finding.confidence < 0.7:
            needs_human_review.append(finding)
        elif finding.confidence < 0.8 or finding.severity in {"info", "low"}:
            warnings.append(finding)
        else:
            confident.append(finding)
    return confident, warnings, needs_human_review


def dedupe_findings(findings: list[Finding]) -> list[Finding]:
    """Deduplicate same file/line/category, keeping the strongest result."""
    best: dict[tuple[str, int | None, str], Finding] = {}
    for finding in findings:
        key = finding.dedupe_key()
        existing = best.get(key)
        if existing is None:
            best[key] = finding
            continue
        existing_score = (existing.confidence, _severity_rank(existing.severity))
        new_score = (finding.confidence, _severity_rank(finding.severity))
        if new_score > existing_score:
            best[key] = finding
    return sorted(best.values(), key=lambda f: (f.file, f.line or 0, f.category, -f.confidence))


def build_metrics(
    *,
    duration_ms: int,
    changed_file_count: int,
    changed_line_count: int,
    findings: list[Finding],
    sandbox_runs: list[SandboxRun],
    redaction_count: int,
) -> ReviewMetrics:
    confident, warnings, needs_human_review = split_findings(findings)
    severity_counts = Counter(f.severity for f in findings)
    exception_counts = Counter(run.error_type for run in sandbox_runs if run.error_type)
    return ReviewMetrics(
        total_duration_ms=duration_ms,
        sandbox_duration_ms=sum(run.duration_ms for run in sandbox_runs),
        tool_call_count=len(sandbox_runs),
        intercept_count=sum(1 for run in sandbox_runs if run.status == "filtered"),
        finding_count=len(confident),
        warning_count=len(warnings),
        needs_human_review_count=len(needs_human_review),
        severity_distribution=dict(sorted(severity_counts.items())),
        exception_type_distribution=dict(sorted(exception_counts.items())),
        redaction_count=redaction_count,
        changed_file_count=changed_file_count,
        changed_line_count=changed_line_count,
    )


def build_report(
    *,
    task_id: str,
    input_ref: str,
    diff_summary: dict[str, Any],
    findings: list[Finding],
    sandbox_runs: list[SandboxRun],
    metrics: ReviewMetrics,
    final_conclusion: str,
) -> dict[str, Any]:
    confident, warnings, needs_human_review = split_findings(findings)
    report = {
        "task_id": task_id,
        "status": "completed",
        "input_ref": input_ref,
        "diff_summary": diff_summary,
        "summary": {
            "final_conclusion": final_conclusion,
            "finding_count": len(confident),
            "warning_count": len(warnings),
            "needs_human_review_count": len(needs_human_review),
            "severity_distribution": metrics.severity_distribution,
        },
        "findings": [finding.to_dict() for finding in confident],
        "warnings": [finding.to_dict() for finding in warnings],
        "needs_human_review": [finding.to_dict() for finding in needs_human_review],
        "filter_intercepts": [
            run.filter_decision.to_dict()
            for run in sandbox_runs
            if run.filter_decision and run.filter_decision.action != "allow"
        ],
        "monitoring": metrics.to_dict(),
        "sandbox_runs": [run.to_dict() for run in sandbox_runs],
        "fix_recommendations": _fix_recommendations(confident + warnings + needs_human_review),
    }
    redacted_report, _ = redact_obj(report)
    return redacted_report


def render_markdown(report: dict[str, Any]) -> str:
    """Render a Markdown report from the JSON report."""
    summary = report["summary"]
    lines = [
        f"# Code Review Report: {report['task_id']}",
        "",
        f"Input: `{report['input_ref']}`",
        "",
        "## Summary",
        "",
        f"- Conclusion: {summary['final_conclusion']}",
        f"- Findings: {summary['finding_count']}",
        f"- Warnings: {summary['warning_count']}",
        f"- Needs human review: {summary['needs_human_review_count']}",
        f"- Severity distribution: `{summary['severity_distribution']}`",
        "",
        "## Findings",
        "",
    ]
    if report["findings"]:
        for item in report["findings"]:
            lines.extend(_finding_md(item))
    else:
        lines.append("No high-confidence findings.")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        for item in report["warnings"]:
            lines.extend(_finding_md(item))
    else:
        lines.append("No warnings.")
    lines.extend(["", "## Needs Human Review", ""])
    if report["needs_human_review"]:
        for item in report["needs_human_review"]:
            lines.extend(_finding_md(item))
    else:
        lines.append("No manual review items.")
    lines.extend(["", "## Filter Intercepts", ""])
    if report["filter_intercepts"]:
        for item in report["filter_intercepts"]:
            lines.append(f"- `{item['action']}` `{item['rule_id']}`: {item['reason']}")
    else:
        lines.append("No filter intercepts.")
    lines.extend(["", "## Monitoring", ""])
    for key, value in report["monitoring"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Sandbox Runs", ""])
    for run in report["sandbox_runs"]:
        lines.append(
            f"- `{run['name']}` runtime=`{run['runtime']}` status=`{run['status']}` "
            f"duration_ms=`{run['duration_ms']}` timed_out=`{run['timed_out']}`"
        )
    lines.extend(["", "## Fix Recommendations", ""])
    if report["fix_recommendations"]:
        for item in report["fix_recommendations"]:
            lines.append(f"- {item}")
    else:
        lines.append("No executable fixes required.")
    lines.append("")
    return "\n".join(lines)


def _finding_md(item: dict[str, Any]) -> list[str]:
    return [
        f"### {item['severity'].upper()} {item['category']}: {item['title']}",
        "",
        f"- Location: `{item['file']}:{item.get('line') or '?'}`",
        f"- Evidence: `{item['evidence']}`",
        f"- Recommendation: {item['recommendation']}",
        f"- Confidence: `{item['confidence']}`",
        f"- Source: `{item['source']}`",
        "",
    ]


def _fix_recommendations(findings: list[Finding]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for finding in findings:
        item = f"{finding.file}:{finding.line or '?'} - {finding.recommendation}"
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _severity_rank(severity: str) -> int:
    return {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}.get(severity, 0)

