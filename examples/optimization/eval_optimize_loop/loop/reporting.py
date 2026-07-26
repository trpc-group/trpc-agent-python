"""Stable JSON and Markdown report writers."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import OptimizationReport

REPORT_JSON_NAME = "optimization_report.json"
REPORT_MARKDOWN_NAME = "optimization_report.md"


def write_reports(report: OptimizationReport, output_dir: Path) -> tuple[Path, Path]:
    """Write JSON and Markdown reports with restrictive local permissions."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / REPORT_JSON_NAME
    markdown_path = output_dir / REPORT_MARKDOWN_NAME
    payload = report.model_dump(mode="json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    _restrict_permissions(json_path)
    _restrict_permissions(markdown_path)
    return json_path, markdown_path


def render_markdown(report: OptimizationReport) -> str:
    """Render the human-readable acceptance summary."""
    gate = "ACCEPT" if report.gate.accepted else "REJECT"
    lines = [
        "# Optimization Report",
        "",
        f"- Status: `{report.status}`",
        f"- Gate: **{gate}**",
        f"- Overfitting: `{report.gate.overfitting}`",
        "",
        "## Scores",
        "",
        "| Split | Baseline | Candidate | Delta | Pass rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for split, delta in report.delta.items():
        lines.append(f"| {split.value} | {_format_score(delta.baseline_score)} | "
                     f"{_format_score(delta.candidate_score)} | {_format_score(delta.score_delta)} | "
                     f"{_pass_rate(report, split)} |")
    lines.extend(["", "## Gate checks", ""])
    lines.extend(f"- {'PASS' if check.passed else 'FAIL'} `{check.name}`: {check.reason}"
                 for check in report.gate.checks)
    lines.extend(["", "## Failure attribution", ""])
    if report.attribution_counts:
        lines.extend(f"- `{category.value}`: {count}" for category, count in report.attribution_counts.items())
    else:
        lines.append("- None")
    lines.extend(["", "## Reasons", ""])
    if report.gate.reasons:
        lines.extend(f"- {reason}" for reason in report.gate.reasons)
    else:
        lines.append("- Accepted")
    lines.extend(["", "## Audit", ""])
    lines.append(f"- Seed: `{report.audit.seed}`")
    lines.append(f"- Cost complete: `{report.cost.cost_complete}`")
    lines.append(f"- Total duration: `{sum(report.audit.stage_durations.values()):.3f}s`")
    return "\n".join(lines) + "\n"


def _pass_rate(report: OptimizationReport, split) -> str:
    snapshot = report.candidate.get(split)
    return f"{snapshot.pass_rate:.3f}" if snapshot else "-"


def _format_score(value: float | None) -> str:
    return "-" if value is None else f"{value:.6f}"


def _restrict_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
