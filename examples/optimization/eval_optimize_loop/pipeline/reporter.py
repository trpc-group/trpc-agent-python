from __future__ import annotations

import json
from pathlib import Path

from .models import OptimizationReport


def write_reports(report: OptimizationReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "optimization_report.json"
    markdown_path = output_dir / "optimization_report.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Optimization Report",
        "",
        f"- Mode: `{report.mode}`",
        f"- Seed: `{report.seed}`",
        f"- Selected candidate: `{report.selected_candidate_id or 'none'}`",
        f"- Source integrity: `{report.source_integrity}`",
        "",
        "## Baseline",
        "",
        "| Split | Pass rate | Aggregate score |",
        "| --- | ---: | ---: |",
    ]
    for name, split in (("train", report.baseline_train), ("validation", report.baseline_validation)):
        if split is not None:
            lines.append(f"| {name} | {split.pass_rate:.3f} | {split.aggregate_score:.3f} |")
    lines.extend([
        "",
        "## Candidates",
        "",
        "| Candidate | Decision | Train score | Validation score |",
        "| --- | --- | ---: | ---: |",
    ])
    candidate_sections: list[str] = []
    for candidate in report.candidates:
        status = "ACCEPT" if candidate.accepted else "REJECT"
        train_score = f"{candidate.train.aggregate_score:.3f}" if candidate.train else "-"
        validation_score = f"{candidate.validation.aggregate_score:.3f}" if candidate.validation else "-"
        lines.append(f"| `{candidate.candidate_id}` | {status} | {train_score} | {validation_score} |")
        section = [
            "",
            f"### `{candidate.candidate_id}`",
            "",
            f"Decision: **{status}**",
            f"Reasons: {'; '.join(candidate.reasons)}",
            "",
            "#### Validation deltas",
            "",
            "| Case | Transition | Score delta | Critical |",
            "| --- | --- | ---: | --- |",
        ]
        for delta in candidate.validation_case_deltas:
            section.append(f"| `{delta.eval_id}` | {delta.transition} | {delta.score_delta:+.3f} | {'yes' if delta.critical else 'no'} |")
        section.extend(["", "#### Gate rules", "", "| Rule | Passed | Actual | Expected |", "| --- | --- | ---: | ---: |"])
        if candidate.gate:
            for rule in candidate.gate.rules:
                section.append(f"| `{rule.rule}` | {'yes' if rule.passed else 'no'} | `{rule.actual}` | `{rule.expected}` |")
        candidate_sections.extend(section)
    lines.extend([
        "",
        "## Validation deltas",
        "",
        "Each candidate section below contains its complete validation case delta table.",
        "",
        "## Gate rules",
        "",
        "Each candidate section below contains its complete gate rule table.",
        *candidate_sections,
    ])
    lines.extend([
        "## Reproduction",
        "",
        "```text",
        "python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --output-dir <output-dir>",
        "```",
    ])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
