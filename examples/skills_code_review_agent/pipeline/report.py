"""Report generation — JSON and Markdown output for review results."""

import json
import textwrap
from datetime import datetime, timezone

from .types import Finding, FilterDecision, ReviewReport, Severity
from .dedup import separate_low_confidence


def generate_json_report(report: ReviewReport) -> str:
    """Generate a JSON-format review report.

    Args:
        report: Complete review report.

    Returns:
        JSON string.
    """
    findings_json = []
    for f in report.findings:
        findings_json.append({
            "severity": f.severity.value,
            "category": f.category.value,
            "file": f.file,
            "line": f.line,
            "title": f.title,
            "evidence": f.evidence,
            "recommendation": f.recommendation,
            "confidence": f.confidence,
            "source": f.source,
        })

    high_conf, low_conf = separate_low_confidence(report.findings, threshold=0.5)

    output = {
        "task_id": report.task_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_findings": len(report.findings),
            "high_confidence": len(high_conf),
            "needs_human_review": len(low_conf),
            "by_severity": _count_by_severity(report.findings),
        },
        "filter_summary": report.filter_summary,
        "sandbox_summary": report.sandbox_summary,
        "telemetry": report.telemetry,
        "findings": findings_json,
        "human_review_items": [
            {"title": f.title, "file": f.file, "line": f.line, "confidence": f.confidence}
            for f in low_conf
        ],
        "recommendations": report.recommendations,
    }
    return json.dumps(output, indent=2, ensure_ascii=False)


def generate_md_report(report: ReviewReport) -> str:
    """Generate a human-readable Markdown review report.

    Args:
        report: Complete review report.

    Returns:
        Markdown string.
    """
    high_conf, low_conf = separate_low_confidence(report.findings, threshold=0.5)
    sev = _count_by_severity(report.findings)

    lines = [
        f"# Code Review Report",
        f"",
        f"**Task ID**: `{report.task_id}`",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Findings | {len(report.findings)} |",
        f"| High Confidence | {len(high_conf)} |",
        f"| Needs Human Review | {len(low_conf)} |",
        f"| Critical | {sev.get('critical', 0)} |",
        f"| High | {sev.get('high', 0)} |",
        f"| Medium | {sev.get('medium', 0)} |",
        f"| Low | {sev.get('low', 0)} |",
        f"| Info | {sev.get('info', 0)} |",
        f"",
    ]

    # Filter summary
    if report.filter_summary:
        lines.append(f"## Filter Summary")
        lines.append(f"")
        lines.append(f"```json")
        lines.append(json.dumps(report.filter_summary, indent=2))
        lines.append(f"```")
        lines.append(f"")

    # Sandbox summary
    if report.sandbox_summary:
        lines.append(f"## Sandbox Execution Summary")
        lines.append(f"")
        lines.append(f"```json")
        lines.append(json.dumps(report.sandbox_summary, indent=2))
        lines.append(f"```")
        lines.append(f"")

    # Findings
    if report.findings:
        lines.append(f"## Findings")
        lines.append(f"")
        for f in sorted(report.findings, key=lambda x: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[x.severity.value],
            -x.confidence,
        )):
            severity_icon = {
                "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"
            }.get(f.severity.value, "❓")
            lines.append(f"### {severity_icon} [{f.severity.value.upper()}] {f.title}")
            lines.append(f"")
            lines.append(f"- **File**: `{f.file}:{f.line}`")
            lines.append(f"- **Category**: {f.category.value}")
            lines.append(f"- **Confidence**: {f.confidence:.0%}")
            lines.append(f"- **Source**: {f.source}")
            lines.append(f"")
            if f.evidence:
                lines.append(f"**Evidence**:")
                lines.append(f"```")
                lines.append(f.evidence)
                lines.append(f"```")
                lines.append(f"")
            lines.append(f"**Recommendation**: {f.recommendation}")
            lines.append(f"")
    else:
        lines.append(f"## Findings")
        lines.append(f"")
        lines.append(f"No issues detected. ✅")
        lines.append(f"")

    # Human review items
    if low_conf:
        lines.append(f"## Items Needing Human Review")
        lines.append(f"")
        lines.append(f"| File | Line | Title | Confidence |")
        lines.append(f"|------|------|-------|------------|")
        for f in low_conf:
            lines.append(f"| `{f.file}` | {f.line} | {f.title} | {f.confidence:.0%} |")
        lines.append(f"")

    # Monitor / Telemetry
    if report.telemetry:
        lines.append(f"## Monitoring")
        lines.append(f"")
        lines.append(f"```json")
        lines.append(json.dumps(report.telemetry, indent=2))
        lines.append(f"```")
        lines.append(f"")

    # Recommendations
    if report.recommendations:
        lines.append(f"## Recommendations")
        lines.append(f"")
        for r in report.recommendations:
            lines.append(f"- {r}")
        lines.append(f"")

    return "\n".join(lines)


def _count_by_severity(findings: list[Finding]) -> dict[str, int]:
    """Count findings by severity level."""
    counts: dict[str, int] = {}
    for f in findings:
        key = f.severity.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _count_by_category(findings: list[Finding]) -> dict[str, int]:
    """Count findings by category."""
    counts: dict[str, int] = {}
    for f in findings:
        key = f.category.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_recommendations(findings: list[Finding]) -> list[str]:
    """Generate actionable recommendations from findings."""
    recs: list[str] = []
    sev = _count_by_severity(findings)
    cat = _count_by_category(findings)

    if sev.get("critical", 0) > 0:
        recs.append(f"Address {sev['critical']} critical finding(s) before merging — "
                     "blocking security issues detected.")
    if sev.get("high", 0) > 0:
        recs.append(f"Review {sev['high']} high-severity finding(s) — "
                     "potential security or stability risks.")
    if cat.get("secret_info", 0) > 0:
        recs.append("Rotate any hardcoded credentials immediately and "
                     "move them to environment variables.")
    if sev.get("medium", 0) > 0:
        recs.append(f"{sev['medium']} medium-severity finding(s) — "
                     "consider fixing before the next release.")
    return recs
