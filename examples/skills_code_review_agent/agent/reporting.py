"""JSON and Markdown report rendering."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Finding
from .models import ReviewReport


def write_reports(report: ReviewReport, output_dir: Path) -> tuple[Path, Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "review_report.json"
    md_path = output_dir / "review_report.md"
    report.output_files.update({"json": _display_path(json_path), "markdown": _display_path(md_path)})
    markdown = render_markdown(report)
    json_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path, markdown


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def render_markdown(report: ReviewReport) -> str:
    lines = [
        "# Code Review Report",
        "",
        f"- Task ID: `{report.task_id}`",
        f"- Status: `{report.status}`",
        f"- Conclusion: {report.conclusion}",
        f"- Finding schema version: {report.finding_schema_version}",
        f"- Confidence thresholds: `{report.confidence_thresholds}`",
        f"- Sandbox policy: `{report.sandbox_policy}`",
        f"- Filter policy: `{report.filter_policy}`",
        f"- Files: {report.input.get('summary', {}).get('file_count', 0)}",
        f"- Findings: {len(report.findings)}",
        f"- Warnings: {len(report.warnings)}",
        f"- Needs human review: {len(report.needs_human_review)}",
        "",
        "## Severity Summary",
        "",
    ]
    severity = report.monitoring.severity_distribution
    if severity:
        for name in ("critical", "high", "medium", "low", "info"):
            if name in severity:
                lines.append(f"- `{name}`: {severity[name]}")
    else:
        lines.append("- No findings.")

    lines.extend(["", "## Findings", ""])
    lines.extend(_finding_lines(report.findings) or ["No high-confidence findings."])
    lines.extend(["", "## Warnings", ""])
    lines.extend(_finding_lines(report.warnings) or ["No warnings."])
    lines.extend(["", "## Needs Human Review", ""])
    lines.extend(_finding_lines(report.needs_human_review) or ["No manual-review items."])

    lines.extend(["", "## Filter Decisions", ""])
    if report.filter_decisions:
        for decision in report.filter_decisions:
            target = decision.path or decision.command
            lines.append(f"- `{decision.decision}` `{decision.policy}` {target}: {decision.reason}")
    else:
        lines.append("No filter decisions recorded.")

    lines.extend(["", "## Filter Interception Summary", ""])
    distribution = report.monitoring.filter_decision_distribution
    denied_or_manual = [d for d in report.filter_decisions if d.decision in {"deny", "needs_human_review"}]
    lines.append(f"- Decision distribution: `{distribution}`")
    lines.append(f"- Interceptions: {len(denied_or_manual)}")
    if denied_or_manual:
        for decision in denied_or_manual:
            target = decision.path or decision.command
            lines.append(f"- `{decision.decision}` `{decision.policy}` {target}: {decision.reason}")
    else:
        lines.append("- No deny or manual-review interceptions.")

    lines.extend(["", "## Skill Audit", ""])
    skill = report.skill_audit
    if skill:
        lines.append(f"- Skill: `{skill.get('name')}`")
        lines.append(f"- Scripts loaded: {skill.get('script_count', 0)}")
        if skill.get("rule_manifest"):
            manifest = skill["rule_manifest"]
            lines.append(f"- Rule manifest: `{manifest['name']}` sha256={manifest['sha256']}")
        for doc in skill.get("docs", []):
            lines.append(f"- Rule doc: `{doc['name']}` sha256={doc['sha256']}")
        for script in skill.get("scripts", []):
            lines.append(f"- Script: `{script['name']}` sha256={script['sha256']}")
    else:
        lines.append("No skill audit recorded.")

    lines.extend(["", "## Sandbox Summary", ""])
    if report.sandbox_runs:
        for run in report.sandbox_runs:
            lines.append(
                f"- `{run.name}` runtime=`{run.runtime}` status=`{run.status}` "
                f"exit={run.exit_code} duration_ms={run.duration_ms}"
            )
    else:
        lines.append("No sandbox runs executed.")

    lines.extend([
        "",
        "## Monitoring",
        "",
        f"- Total duration ms: {report.monitoring.total_duration_ms}",
        f"- Sandbox duration ms: {report.monitoring.sandbox_duration_ms}",
        f"- Stage durations ms: `{report.monitoring.stage_durations_ms}`",
        f"- Risk level: `{report.monitoring.risk_level}`",
        f"- Tool calls: {report.monitoring.tool_call_count}",
        f"- Filter decisions: {report.monitoring.filter_decision_count}",
        f"- Filter interceptions: {report.monitoring.interception_count}",
        f"- Filter decision distribution: `{report.monitoring.filter_decision_distribution}`",
        f"- Redactions: {report.monitoring.redaction_count}",
        f"- Deduped findings: {report.monitoring.deduped_finding_count}",
        f"- Ignored findings: {report.monitoring.ignored_finding_count}",
        f"- Exception distribution: `{report.monitoring.exception_distribution}`",
    ])
    return "\n".join(lines) + "\n"


def _finding_lines(findings: list[Finding]) -> list[str]:
    lines: list[str] = []
    for finding in findings:
        lines.extend([
            f"- `{finding.severity}` `{finding.category}` `{finding.finding_id}` "
            f"{finding.file}:{finding.line} - {finding.title}",
            f"  Evidence: `{finding.evidence}`",
            f"  Recommendation: {finding.recommendation}",
            f"  Confidence: {finding.confidence:.2f}; Source: `{finding.source}`",
        ])
        if finding.hunk_header:
            lines.append(f"  Hunk: `{finding.hunk_header}`")
        if finding.context_before or finding.context_after:
            lines.append(f"  Context before: `{finding.context_before}`")
            lines.append(f"  Context after: `{finding.context_after}`")
    return lines
