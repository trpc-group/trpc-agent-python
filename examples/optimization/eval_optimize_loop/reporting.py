from __future__ import annotations

import json
import os

from .models import PipelineResult


def _escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_reports(result: PipelineResult, output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)

    # JSON report
    json_path = os.path.join(output_dir, "optimization_report.json")
    json_str = result.model_dump_json(indent=2, by_alias=True)
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_str)

    # Markdown report
    md_path = os.path.join(output_dir, "optimization_report.md")
    lines = _build_markdown(result)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _build_markdown(result: PipelineResult) -> list[str]:
    lines: list[str] = []

    lines.append("# Optimization Report")
    lines.append("")

    # Verdict
    verdict_icon = "✓" if result.gate_decision == "ACCEPT" else "✗"
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"**{verdict_icon} {result.gate_decision}**")
    lines.append("")
    for reason in result.gate_reasons:
        lines.append(f"- {reason}")
    lines.append("")

    # Pass Rates
    lines.append("## Pass Rates")
    lines.append("")
    lines.append("| Split | Baseline | Candidate | Delta |")
    lines.append("|---|---|---|---|")
    for split_name in ["train", "val"]:
        base = result.baseline.get(split_name)
        cand = result.candidate.get(split_name)
        base_pr = base.pass_rate if base else 0.0
        cand_pr = cand.pass_rate if cand else 0.0
        delta_val = cand_pr - base_pr
        lines.append(f"| {split_name} | {base_pr:.4f} | {cand_pr:.4f} | {delta_val:+.4f} |")
    lines.append("")

    # Metric breakdown
    if result.baseline:
        base_ref = result.baseline.get("val") or result.baseline.get("train")
        cand_ref = result.candidate.get("val") or result.candidate.get("train")
        split_label = "val" if result.baseline.get("val") else "train"
        if base_ref and base_ref.metric_breakdown:
            lines.append(f"### Metric Breakdown ({split_label})")
            lines.append("")
            lines.append("| Metric | Baseline | Candidate | Delta |")
            lines.append("|---|---|---|---|")
            all_metrics = sorted(set(base_ref.metric_breakdown.keys()) | set(cand_ref.metric_breakdown.keys()) if cand_ref else set())
            for m in all_metrics:
                b = base_ref.metric_breakdown.get(m, 0.0)
                c = cand_ref.metric_breakdown.get(m, 0.0) if cand_ref else 0.0
                lines.append(f"| {_escape_md(m)} | {b:.4f} | {c:.4f} | {c-b:+.4f} |")
            lines.append("")

    # Per-Case Delta
    lines.append("## Per-Case Delta")
    lines.append("")
    for split_name in ["train", "val"]:
        lines.append(f"### {split_name} set")
        lines.append("")
        lines.append("| Case ID | Baseline | Candidate | Status |")
        lines.append("|---|---|---|---|")
        base_sr = result.baseline.get(split_name)
        cand_sr = result.candidate.get(split_name)

        if base_sr and cand_sr:
            all_cases = sorted(set(base_sr.per_case.keys()) | set(cand_sr.per_case.keys()))
            for case_id in all_cases:
                base_passed = base_sr.per_case[case_id].passed if case_id in base_sr.per_case else False
                cand_passed = cand_sr.per_case[case_id].passed if case_id in cand_sr.per_case else False
                if not base_passed and cand_passed:
                    status = "newly passing"
                elif base_passed and not cand_passed:
                    status = "newly failing"
                elif base_passed and cand_passed:
                    status = "passed (both)"
                else:
                    status = "failed (both)"
                status_str = "PASS" if base_passed else "FAIL"
                cand_status_str = "PASS" if cand_passed else "FAIL"
                lines.append(f"| {_escape_md(case_id)} | {status_str} | {cand_status_str} | {status} |")
        lines.append("")

    # Failure Attribution
    lines.append("## Failure Attribution")
    lines.append("")
    fa = result.failure_attribution
    lines.append(f"- Total cases: {fa.total_cases}")
    lines.append(f"- Failed (baseline train): {fa.failed_cases}")
    lines.append("")
    if fa.categories:
        lines.append("| Category | Count | Case IDs |")
        lines.append("|---|---|---|")
        for cat_name, cat in sorted(fa.categories.items()):
            lines.append(f"| {_escape_md(cat_name)} | {cat.count} | {', '.join(cat.case_ids)} |")
    else:
        lines.append("No failures to attribute.")
    lines.append("")

    # Gate Results
    lines.append("## Gate Results")
    lines.append("")
    for reason in result.gate_reasons:
        lines.append(f"- {reason}")
    lines.append("")

    # Overfitting Check
    lines.append("## Overfitting Check")
    lines.append("")
    if result.overfitting_warning:
        lines.append("**WARNING: Possible overfitting detected.**")
        lines.append("")
        lines.append(f"- Train pass rate delta: {result.delta.train_pass_rate_delta:+.4f}")
        lines.append(f"- Val pass rate delta: {result.delta.val_pass_rate_delta:+.4f}")
    else:
        lines.append("No overfitting detected.")
    lines.append("")

    # Audit
    lines.append("## Audit")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Mode | {result.mode} |")
    lines.append(f"| Duration | {result.duration_seconds:.2f}s |")
    lines.append(f"| Cost | ${result.cost_usd:.4f} |")
    lines.append(f"| Seed | {result.seed} |")
    lines.append(f"| Started | {result.started_at} |")
    lines.append(f"| Finished | {result.finished_at} |")
    lines.append(f"| Schema version | {result.schema_version} |")
    lines.append("")

    return lines
