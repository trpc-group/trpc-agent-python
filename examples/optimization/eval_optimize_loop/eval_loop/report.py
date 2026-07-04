"""Report construction and rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .attribution import summarize_failures
from .schemas import CandidatePrompt
from .schemas import CaseDelta
from .schemas import EvalResult
from .schemas import GateDecision
from .schemas import OptimizationReport
from .schemas import to_jsonable


REPRODUCIBILITY_COMMAND = (
    "python examples/optimization/eval_optimize_loop/run_pipeline.py "
    "--train examples/optimization/eval_optimize_loop/data/train.evalset.json "
    "--val examples/optimization/eval_optimize_loop/data/val.evalset.json "
    "--optimizer-config examples/optimization/eval_optimize_loop/data/optimizer.json "
    "--prompt examples/optimization/eval_optimize_loop/prompts/baseline_system_prompt.txt "
    "--output-dir /tmp/eval-optimize-loop "
    "--fake-model --fake-judge --trace"
)


def compute_case_deltas(
    *,
    candidate_id: str,
    baseline_train: EvalResult,
    baseline_validation: EvalResult,
    candidate_train: EvalResult,
    candidate_validation: EvalResult,
) -> list[CaseDelta]:
    deltas: list[CaseDelta] = []
    for baseline, candidate in ((baseline_train, candidate_train), (baseline_validation, candidate_validation)):
        candidate_by_id = candidate.by_case_id()
        for baseline_case in baseline.cases:
            candidate_case = candidate_by_id[baseline_case.case_id]
            delta = round(candidate_case.score - baseline_case.score, 6)
            deltas.append(
                CaseDelta(
                    candidate_id=candidate_id,
                    case_id=baseline_case.case_id,
                    split=baseline_case.split,
                    baseline_score=baseline_case.score,
                    candidate_score=candidate_case.score,
                    delta=delta,
                    baseline_passed=baseline_case.passed,
                    candidate_passed=candidate_case.passed,
                    regression=delta < 0,
                )
            )
    return deltas


def build_report(
    *,
    run: dict[str, Any],
    baseline_train: EvalResult,
    baseline_validation: EvalResult,
    candidate_records: list[dict[str, Any]],
    per_case_deltas: list[CaseDelta],
    gate_decisions: list[GateDecision],
    selected_candidate: str | None,
    audit: dict[str, Any],
) -> OptimizationReport:
    all_results: list[EvalResult] = [baseline_train, baseline_validation]
    for record in candidate_records:
        all_results.append(record["train_result"])
        all_results.append(record["validation_result"])
    return OptimizationReport(
        schema_version="eval_optimize_loop.v1",
        run=run,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidates=candidate_records,
        per_case_deltas=per_case_deltas,
        failure_attribution_summary=summarize_failures(all_results),
        gate_decisions=gate_decisions,
        selected_candidate=selected_candidate,
        audit=audit,
    )


def write_reports(report: OptimizationReport, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "optimization_report.json"
    md_path = output_path / "optimization_report.md"
    json_path.write_text(report_to_json(report), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def report_to_json(report: OptimizationReport) -> str:
    return json.dumps(to_jsonable(report), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def render_markdown(report: OptimizationReport) -> str:
    decision_by_id = {decision.candidate_id: decision for decision in report.gate_decisions}
    lines = [
        "# Evaluation + Optimization Report",
        "",
        "## Final Decision",
        "",
    ]
    if report.selected_candidate:
        lines.append(f"Selected candidate: `{report.selected_candidate}`.")
    else:
        lines.append("No candidate was accepted.")

    lines.extend([
        "",
        "## Gate Reasons",
        "",
    ])
    for decision in report.gate_decisions:
        verdict = "accepted" if decision.accepted else "rejected"
        lines.append(f"### {decision.candidate_id} ({verdict})")
        for reason in decision.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.extend([
        "## Baseline vs Candidate Scores",
        "",
        "| prompt | train score | validation score | gate |",
        "| --- | ---: | ---: | --- |",
        f"| baseline | {report.baseline_train.score:.3f} | {report.baseline_validation.score:.3f} | n/a |",
    ])
    for record in report.candidates:
        candidate: CandidatePrompt = record["candidate"]
        gate = decision_by_id[candidate.candidate_id]
        verdict = "accept" if gate.accepted else "reject"
        lines.append(
            f"| {candidate.candidate_id} | {record['train_result'].score:.3f} | "
            f"{record['validation_result'].score:.3f} | {verdict} |"
        )

    lines.extend([
        "",
        "## Per-Case Delta",
        "",
        "| candidate | split | case | baseline | candidate | delta | passed -> passed |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ])
    for delta in report.per_case_deltas:
        lines.append(
            f"| {delta.candidate_id} | {delta.split} | {delta.case_id} | "
            f"{delta.baseline_score:.3f} | {delta.candidate_score:.3f} | "
            f"{delta.delta:+.3f} | {delta.baseline_passed} -> {delta.candidate_passed} |"
        )

    summary = report.failure_attribution_summary
    lines.extend([
        "",
        "## Failure Attribution Summary",
        "",
        f"Total failed case evaluations: {summary['total_failed_cases']}",
        "",
        "| category | count |",
        "| --- | ---: |",
    ])
    by_category = summary.get("by_category", {})
    if by_category:
        for category, count in by_category.items():
            lines.append(f"| {category} | {count} |")
    else:
        lines.append("| none | 0 |")

    lines.extend([
        "",
        "## Prompt Diff",
        "",
    ])
    for record in report.candidates:
        candidate = record["candidate"]
        lines.extend([
            f"### {candidate.candidate_id}",
            "",
            "```diff",
            candidate.prompt_diff,
            "```",
            "",
        ])

    lines.extend([
        "## Reproducibility",
        "",
        "```bash",
        REPRODUCIBILITY_COMMAND,
        "```",
        "",
    ])
    return "\n".join(lines)
