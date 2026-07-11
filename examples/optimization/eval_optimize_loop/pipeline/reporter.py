from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .config import sanitize_config
from .models import OptimizationReport


def _json_safe(value: object) -> object:
    if isinstance(value, BaseModel):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize_config(_json_safe(payload)), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_audit_artifacts(report: OptimizationReport, output_dir: Path) -> None:
    """Persist normalized, secret-free evidence used by the Gate decision."""
    audit = output_dir / "audit"
    refs = report.audit_references
    _write_json(output_dir / refs.raw_reports_path, {"baseline_train": report.baseline_train, "baseline_validation": report.baseline_validation})
    _write_json(
        output_dir / refs.normalized_reports_path,
        {
            "baseline_train": report.baseline_train,
            "baseline_validation": report.baseline_validation,
            "candidate_train_validation": [
                {"candidate_id": candidate.candidate_id, "train": candidate.train, "validation": candidate.validation}
                for candidate in report.candidates
            ],
        },
    )
    _write_json(output_dir / refs.candidate_reports_path, report.candidates)
    _write_json(output_dir / refs.gate_decisions_path, {"selected_candidate_id": report.selected_candidate_id, "decisions": [{"candidate_id": candidate.candidate_id, "gate": candidate.gate} for candidate in report.candidates]})
    # The run functions may write richer snapshots before this call.  Keep a
    # deterministic placeholder so every mode has the documented references.
    config_path = output_dir / refs.config_snapshot_path
    if not config_path.exists():
        _write_json(config_path, {"mode": report.mode, "seed": report.seed, "run_metadata": report.run_metadata})
    environment_path = output_dir / refs.environment_snapshot_path
    if not environment_path.exists():
        _write_json(environment_path, {"mode": report.mode, "seed": report.seed})


def write_reports(report: OptimizationReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_audit_artifacts(report, output_dir)
    json_path = output_dir / "optimization_report.json"
    markdown_path = output_dir / "optimization_report.md"
    json_path.write_text(json.dumps(sanitize_config(report.model_dump(mode="json")), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Optimization Report",
        "",
        f"- Mode: `{report.mode}`",
        f"- Seed: `{report.seed}`",
        f"- Selected candidate: `{report.selected_candidate_id or 'none'}`",
        f"- Source integrity: `{report.source_integrity}`",
        f"- Audit evidence: `{report.audit_references.gate_decisions_path}`",
        "",
        "## Baseline",
        "",
        "| Split | Pass rate | Aggregate score |",
        "| --- | ---: | ---: |",
    ]
    for name, split in (("train", report.baseline_train), ("validation", report.baseline_validation)):
        if split is not None:
            lines.append(f"| {name} | {split.pass_rate:.3f} | {split.aggregate_score:.3f} |")
    baseline_failures = [
        case
        for split in (report.baseline_train, report.baseline_validation)
        if split is not None
        for case in split.cases
        if case.failure_attribution is not None
    ]
    if baseline_failures:
        lines.extend(["", "## Baseline failure attribution", "", "| Case | Primary type | Source | Evidence |", "| --- | --- | --- | --- |"])
        for case in baseline_failures:
            attribution = case.failure_attribution
            lines.append(
                f"| `{case.eval_id}` | `{attribution.primary_type.value}` | `{attribution.source}` | {'; '.join(attribution.evidence)} |"
            )
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
            if candidate.gate.warnings:
                section.append(f"\nWarnings: {'; '.join(candidate.gate.warnings)}")
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
        f"python examples/optimization/eval_optimize_loop/run_pipeline.py --mode {report.mode} --output-dir <output-dir>",
        "```",
    ])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
