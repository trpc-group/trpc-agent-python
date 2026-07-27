"""Stable JSON and Markdown report writers."""

from __future__ import annotations

import json
import os
import tempfile
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
    contents = {
        json_path: json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        markdown_path: render_markdown(report),
    }
    _publish_reports(contents)
    return json_path, markdown_path


def _publish_reports(contents: dict[Path, str]) -> None:
    staged: dict[Path, Path] = {}
    try:
        for path, content in contents.items():
            staged[path] = _stage_report(path, content)
    except Exception:
        for staged_path in staged.values():
            staged_path.unlink(missing_ok=True)
        raise
    previous = {path: path.read_bytes() if path.exists() else None for path in contents}
    replaced: list[Path] = []
    try:
        for path, staged_path in staged.items():
            os.replace(staged_path, path)
            replaced.append(path)
    except Exception:
        _restore_reports(replaced, previous)
        raise
    finally:
        for staged_path in staged.values():
            staged_path.unlink(missing_ok=True)


def _stage_report(path: Path, content: str) -> Path:
    return _stage_bytes(path, content.encode("utf-8"))


def _stage_bytes(path: Path, content: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    staged_path = Path(temporary_name)
    staged_path.write_bytes(content)
    _restrict_permissions(staged_path)
    return staged_path


def _restore_reports(replaced: list[Path], previous: dict[Path, bytes | None]) -> None:
    for path in reversed(replaced):
        content = previous[path]
        if content is None:
            path.unlink(missing_ok=True)
            continue
        staged_path = _stage_bytes(path, content)
        try:
            os.replace(staged_path, path)
        finally:
            staged_path.unlink(missing_ok=True)


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
