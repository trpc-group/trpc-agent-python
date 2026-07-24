#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Atomic JSON and Markdown report persistence."""

from __future__ import annotations

import os
from pathlib import Path

from .models import OptimizationReport


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_reports(report: OptimizationReport, output_dir: Path) -> None:
    """Persist both report formats without exposing partial files."""
    json_text = report.model_dump_json(indent=2) + "\n"
    _atomic_write(output_dir / "optimization_report.json", json_text)
    _atomic_write(output_dir / "optimization_report.md", render_markdown(report))


def render_markdown(report: OptimizationReport) -> str:
    """Render the decision-first human report."""
    selected = report.candidate
    lines = [
        "# Evaluation + Optimization Regression Report",
        "",
        f"- **Decision:** `{report.status}`",
        f"- **Selected candidate:** `{report.selected_candidate_id or 'none'}`",
        f"- **Optimizer:** `{report.optimizer.algorithm}` / `{report.optimizer.status}`",
        f"- **Seed:** `{report.audit.seed}`",
        "",
        "## Baseline",
        "",
        "| Split | Pass rate | Average score | Cases |",
        "| --- | ---: | ---: | ---: |",
        (f"| Train | {report.baseline.train.pass_rate:.3f} | "
         f"{report.baseline.train.average_score:.3f} | {len(report.baseline.train.cases)} |"),
        (f"| Validation | {report.baseline.validation.pass_rate:.3f} | "
         f"{report.baseline.validation.average_score:.3f} | "
         f"{len(report.baseline.validation.cases)} |"),
        "",
        "## Candidate matrix",
        "",
        ("| Candidate | Train pass | Validation pass | Validation delta | "
         "Paired CI | Gate | Overfit | Pareto |"),
        "| --- | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for item in report.candidates:
        ci = item.delta.validation.paired_pass_rate_ci
        lines.append(f"| `{item.candidate_id}` | {item.train.pass_rate:.3f} | "
                     f"{item.validation.pass_rate:.3f} | {item.delta.validation.pass_rate_delta:+.3f} | "
                     f"[{ci.lower:+.3f}, {ci.upper:+.3f}] | "
                     f"{'PASS' if item.gate.accepted else 'FAIL'} | "
                     f"{item.gate.overfitting_detected} | {item.pareto_optimal} |")
    lines.extend([
        "",
        "## Candidate",
        "",
    ])
    if selected is None:
        lines.append("No candidate passed the configured gate.")
    else:
        lines.extend([
            f"Candidate `{selected.candidate_id}` was independently re-evaluated.",
            "",
            "| Split | Baseline | Candidate | Delta |",
            "| --- | ---: | ---: | ---: |",
            (f"| Train pass rate | {report.baseline.train.pass_rate:.3f} | "
             f"{selected.train.pass_rate:.3f} | {selected.delta.train.pass_rate_delta:+.3f} |"),
            (f"| Validation pass rate | {report.baseline.validation.pass_rate:.3f} | "
             f"{selected.validation.pass_rate:.3f} | "
             f"{selected.delta.validation.pass_rate_delta:+.3f} |"),
            "",
            ("Paired bootstrap interval: "
             f"`[{selected.delta.validation.paired_pass_rate_ci.lower:+.3f}, "
             f"{selected.delta.validation.paired_pass_rate_ci.upper:+.3f}]` "
             f"at {selected.delta.validation.paired_pass_rate_ci.confidence_level:.0%} "
             "confidence."),
            "",
            "### Per-case delta",
            "",
            "| Case | Split | Status | Score delta |",
            "| --- | --- | --- | ---: |",
        ])
        for split_delta in (selected.delta.train, selected.delta.validation):
            for case in split_delta.cases:
                lines.append(f"| `{case.case_id}` | {split_delta.split} | {case.status} | "
                             f"{case.score_delta:+.3f} |")

    lines.extend([
        "",
        "## Gate",
        "",
        "| Candidate | Check | Required | Result | Actual | Expected |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for item in report.candidates:
        for check in item.gate.checks:
            lines.append(f"| `{item.candidate_id}` | `{check.name}` | {check.required} | "
                         f"{'PASS' if check.passed else 'FAIL'} | `{check.actual}` | "
                         f"`{check.expected}` |")

    lines.extend([
        "",
        "## Failure attribution",
        "",
        (f"Explained {report.failure_attribution.explained_failed_cases}/"
         f"{report.failure_attribution.total_failed_cases} failed cases "
         f"({report.failure_attribution.coverage_rate:.1%})."),
        "",
        "| Case | Category | Explanation |",
        "| --- | --- | --- |",
    ])
    for case_id, reasons in report.failure_attribution.by_case.items():
        for reason in reasons:
            lines.append(f"| `{case_id}` | `{reason.category}` | {reason.explanation} |")

    lines.extend([
        "",
        "## Audit",
        "",
        f"- Run id: `{report.audit.run_id}`",
        f"- Duration: `{report.audit.duration_seconds:.3f}s`",
        f"- Metric calls: `{report.optimizer.resources.metric_calls}`",
        f"- Tokens: `{report.optimizer.resources.total_tokens}`",
        f"- Cost measurement: `{report.optimizer.resources.cost_measurement}`",
        f"- Optimizer artifacts: `{report.optimizer.artifact_dir}`",
        "",
        "### Candidate evaluation resources",
        "",
        "| Candidate | Metric calls | Judge calls | Tokens | P95 latency | Duration | Cost | Cost measurement |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for item in report.candidates:
        usage = item.audit.resources
        cost = "unavailable" if usage.cost_usd is None else f"${usage.cost_usd:.4f}"
        latency = "unavailable" if usage.p95_latency_ms is None else f"{usage.p95_latency_ms:.1f} ms"
        lines.append(f"| `{item.candidate_id}` | {usage.metric_calls} | {usage.judge_calls} | "
                     f"{usage.total_tokens} | {latency} | "
                     f"{usage.duration_seconds:.3f} s | {cost} | `{usage.cost_measurement}` |")
    lines.append("")
    return "\n".join(lines)
