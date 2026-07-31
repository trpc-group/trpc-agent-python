# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report assembly and rendering for the code-review example."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import FilterDecision
from .models import FilterEvent
from .models import Finding
from .models import FindingCategory
from .models import FindingSeverity
from .models import FindingSource
from .models import InputSummary
from .models import ReviewMetrics
from .models import ReviewReport
from .models import SandboxRun
from .models import SandboxStatus
from .sanitizer import redact_mapping
from .sanitizer import redact_text

_WARNING_CONFIDENCE_THRESHOLD = 0.7


def dedupe_findings(findings: list[Finding]) -> list[Finding]:
    """Return findings with stable fingerprints and duplicates removed."""
    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        fingerprint = finding.fingerprint or _fallback_fingerprint(finding)
        finding.fingerprint = fingerprint
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(_redact_finding(finding))
    return unique


def route_findings(
    findings: list[Finding],
    filter_events: list[FilterEvent],
    sandbox_runs: list[SandboxRun],
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Split findings into report findings, warnings, and human-review items."""
    routed_findings: list[Finding] = []
    warnings: list[Finding] = []
    needs_human_review: list[Finding] = []
    for finding in dedupe_findings(findings):
        if finding.confidence < _WARNING_CONFIDENCE_THRESHOLD:
            warnings.append(finding)
        else:
            routed_findings.append(finding)
    for event in filter_events:
        if event.decision is FilterDecision.NEEDS_HUMAN_REVIEW:
            needs_human_review.append(_governance_finding(event))
    for sandbox_run in sandbox_runs:
        if sandbox_run.status is not SandboxStatus.SUCCESS:
            needs_human_review.append(_sandbox_finding(sandbox_run))
    return routed_findings, warnings, dedupe_findings(needs_human_review)


def build_review_report(
    *,
    task_id: str,
    input_summary: InputSummary,
    findings: list[Finding],
    filter_events: list[FilterEvent],
    sandbox_runs: list[SandboxRun],
    total_duration_ms: int = 0,
) -> ReviewReport:
    """Build the final machine-readable review report."""
    routed_findings, warnings, needs_human_review = route_findings(findings, filter_events, sandbox_runs)
    all_items = [*routed_findings, *warnings, *needs_human_review]
    metrics = ReviewMetrics(
        total_duration_ms=total_duration_ms,
        sandbox_duration_ms=sum(item.duration_ms for item in sandbox_runs),
        tool_call_count=len(sandbox_runs),
        interception_count=sum(1 for item in filter_events if item.decision is not FilterDecision.ALLOW),
        finding_count=len(routed_findings),
        warning_count=len(warnings),
        needs_human_review_count=len(needs_human_review),
        severity_distribution=_count_by(all_items, "severity"),
        category_distribution=_count_by(all_items, "category"),
        exception_distribution=_exception_distribution(sandbox_runs),
    )
    conclusion = _conclusion(routed_findings, warnings, needs_human_review)
    return ReviewReport(
        task_id=task_id,
        findings=routed_findings,
        warnings=warnings,
        needs_human_review=needs_human_review,
        stats={
            "input_summary": redact_mapping(input_summary.to_dict()),
            "files": input_summary.file_count,
            "hunks": input_summary.hunk_count,
            "added_lines": input_summary.added_lines,
            "deleted_lines": input_summary.deleted_lines,
        },
        metrics=metrics,
        interceptions=filter_events,
        sandbox_runs=sandbox_runs,
        conclusion=conclusion,
    )


def write_review_report(output_dir: str | Path, report: ReviewReport) -> tuple[Path, Path]:
    """Write JSON and Markdown reports and return their paths."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "review_report.json"
    md_path = root / "review_report.md"
    rendered_json = json.dumps(redact_mapping(report.to_dict()), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    json_path.write_text(rendered_json, encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, md_path


def _render_markdown(report: ReviewReport) -> str:
    lines = [
        "# Code Review Report",
        "",
        f"- Task: `{report.task_id}`",
        f"- Conclusion: {redact_text(report.conclusion)}",
        f"- Findings: {len(report.findings)}",
        f"- Warnings: {len(report.warnings)}",
        f"- Needs human review: {len(report.needs_human_review)}",
        "",
        "## Metrics",
        "",
        f"- Total duration: {report.metrics.total_duration_ms} ms",
        f"- Sandbox duration: {report.metrics.sandbox_duration_ms} ms",
        f"- Tool calls: {report.metrics.tool_call_count}",
        f"- Interceptions: {report.metrics.interception_count}",
        f"- Severity distribution: `{json.dumps(report.metrics.severity_distribution, sort_keys=True)}`",
        f"- Category distribution: `{json.dumps(report.metrics.category_distribution, sort_keys=True)}`",
        "",
    ]
    lines.extend(_render_findings("Findings", report.findings))
    lines.extend(_render_findings("Warnings", report.warnings))
    lines.extend(_render_findings("Needs Human Review", report.needs_human_review))
    lines.extend(_render_filter_events(report.interceptions))
    lines.extend(_render_sandbox_runs(report.sandbox_runs))
    lines.extend([
        "## Fix Suggestions",
        "",
        "- Address critical and high findings before merging.",
        "- Add focused tests for changed source behavior.",
        "- Review any governance or sandbox items before trusting execution output.",
        "",
    ])
    return "\n".join(lines)


def _render_findings(title: str, findings: list[Finding]) -> list[str]:
    lines = [f"## {title}", ""]
    if not findings:
        return [*lines, "None.", ""]
    for item in findings:
        location = f"{item.file}:{item.line}" if item.line is not None else item.file
        lines.extend([
            f"- **{redact_text(item.title)}** `{item.severity.value}` `{item.category.value}`",
            f"  - Location: `{redact_text(location)}`",
            f"  - Evidence: {redact_text(item.evidence)}",
            f"  - Recommendation: {redact_text(item.recommendation)}",
        ])
    lines.append("")
    return lines


def _render_filter_events(events: list[FilterEvent]) -> list[str]:
    lines = ["## Filter Events", ""]
    if not events:
        return [*lines, "None.", ""]
    for event in events:
        lines.append(f"- `{event.decision.value}` `{event.reason_code.value}`: {redact_text(event.reason)}")
    lines.append("")
    return lines


def _render_sandbox_runs(runs: list[SandboxRun]) -> list[str]:
    lines = ["## Sandbox Runs", ""]
    if not runs:
        return [*lines, "None.", ""]
    for run in runs:
        lines.append(f"- `{run.runtime.value}` `{run.status.value}` exit={run.exit_code} duration={run.duration_ms}ms")
    lines.append("")
    return lines


def _governance_finding(event: FilterEvent) -> Finding:
    evidence = f"{event.reason_code.value}: {event.reason}"
    return Finding(
        severity=FindingSeverity.MEDIUM,
        category=FindingCategory.GOVERNANCE,
        file=event.script_path or event.cwd or "<governance>",
        line=None,
        title="Execution requires human review",
        evidence=redact_text(evidence),
        recommendation="Review the governance decision before relying on this execution path.",
        confidence=1.0,
        source=FindingSource.FILTER,
        fingerprint=_hash("governance", event.task_id, event.reason_code.value, event.target),
    )


def _sandbox_finding(run: SandboxRun) -> Finding:
    evidence = run.stderr or run.error_type or run.status.value
    return Finding(
        severity=FindingSeverity.MEDIUM,
        category=FindingCategory.SANDBOX,
        file="<sandbox>",
        line=None,
        title="Sandbox execution did not complete successfully",
        evidence=redact_text(evidence),
        recommendation="Inspect sandbox status, diagnostics, and execution policy before trusting results.",
        confidence=1.0,
        source=FindingSource.SANDBOX,
        fingerprint=_hash("sandbox", run.task_id, run.runtime.value, run.status.value, run.error_type),
    )


def _redact_finding(finding: Finding) -> Finding:
    finding.evidence = redact_text(finding.evidence)
    finding.recommendation = redact_text(finding.recommendation)
    finding.title = redact_text(finding.title)
    return finding


def _fallback_fingerprint(finding: Finding) -> str:
    return _hash(
        finding.category.value,
        finding.file,
        str(finding.line or ""),
        finding.title,
        finding.evidence,
    )


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _count_by(findings: list[Finding], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        value = getattr(finding, attr)
        key = value.value if hasattr(value, "value") else str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _exception_distribution(sandbox_runs: list[SandboxRun]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in sandbox_runs:
        if run.error_type:
            counts[run.error_type] = counts.get(run.error_type, 0) + 1
    return counts


def _conclusion(findings: list[Finding], warnings: list[Finding], needs_human_review: list[Finding]) -> str:
    if any(item.severity in {FindingSeverity.CRITICAL, FindingSeverity.HIGH} for item in findings):
        return "High risk findings require changes before merge."
    if needs_human_review:
        return "Human review is required for execution or sandbox uncertainty."
    if findings or warnings:
        return "Review completed with findings or warnings."
    return "Review completed with no findings."
