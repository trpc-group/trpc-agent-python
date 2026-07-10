"""Report construction and rendering."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .attribution import summarize_failures
from .schemas import CandidatePrompt
from .schemas import CaseDelta
from .schemas import CostSummary
from .schemas import EvalResult
from .schemas import GateDecision
from .schemas import OptimizationReport
from .schemas import OptimizationRound
from .schemas import WritebackResult
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
ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class RunArtifactPaths:
    """Locations written by the audit-first report lifecycle."""

    output_dir: Path
    run_dir: Path
    prewrite_json: Path
    prewrite_markdown: Path
    audit_json: Path
    final_json: Path
    final_markdown: Path
    writeback_json: Path
    writeback_journal: Path
    top_level_json: Path
    top_level_markdown: Path


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
            delta_type = _delta_type(
                baseline_passed=baseline_case.passed,
                candidate_passed=candidate_case.passed,
                delta=delta,
            )
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
                    delta_type=delta_type,
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
    rounds: list[OptimizationRound] | None = None,
    cost_summary: CostSummary | None = None,
    writeback: WritebackResult | None = None,
) -> OptimizationReport:
    all_results: list[EvalResult] = [baseline_train, baseline_validation]
    for record in candidate_records:
        all_results.append(record["train_result"])
        all_results.append(record["validation_result"])
    return OptimizationReport(
        schema_version="eval_optimize_loop.v1",
        run=run,
        baseline={"train": baseline_train, "validation": baseline_validation},
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidates=candidate_records,
        delta={"per_case": per_case_deltas},
        per_case_deltas=per_case_deltas,
        failure_attribution_summary=summarize_failures(all_results),
        gate_decisions=gate_decisions,
        selected_candidate=selected_candidate,
        audit=audit,
        rounds=list(rounds or []),
        cost_summary=cost_summary or CostSummary(),
        writeback=writeback or WritebackResult(status="not_requested"),
    )


def write_reports(report: OptimizationReport, output_dir: str | Path) -> tuple[Path, Path]:
    """Compatibility helper for callers that do not perform source writeback."""

    paths = prepare_run_artifacts(report, output_dir)
    finalize_run_artifacts(report, paths)
    return paths.top_level_json, paths.top_level_markdown


def prepare_run_artifacts(
    report: OptimizationReport,
    output_dir: str | Path,
) -> RunArtifactPaths:
    """Persist a complete pre-write audit before any source prompt commit."""

    output_path = Path(output_dir)
    run_id = _safe_artifact_name(str(report.run.get("run_id") or "run"))
    run_dir = output_path / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = RunArtifactPaths(
        output_dir=output_path,
        run_dir=run_dir,
        prewrite_json=run_dir / "pre_write_report.json",
        prewrite_markdown=run_dir / "pre_write_report.md",
        audit_json=run_dir / "audit.json",
        final_json=run_dir / "optimization_report.json",
        final_markdown=run_dir / "optimization_report.md",
        writeback_json=run_dir / "writeback.json",
        writeback_journal=run_dir / "writeback_journal.json",
        top_level_json=output_path / "optimization_report.json",
        top_level_markdown=output_path / "optimization_report.md",
    )
    paths.prewrite_json.write_text(report_to_json(report), encoding="utf-8")
    paths.prewrite_markdown.write_text(render_markdown(report), encoding="utf-8")
    paths.audit_json.write_text(
        json.dumps(
            to_jsonable(report.audit),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    journal = dict(report.audit.get("writeback_journal", {}))
    if journal.get("state") == "pending":
        journal["state"] = "prepared"
    paths.writeback_journal.write_text(
        json.dumps(
            to_jsonable(journal),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    write_audit_artifacts(report, output_path)
    return paths


def finalize_run_artifacts(report: OptimizationReport, paths: RunArtifactPaths) -> None:
    """Persist final writeback state and refresh top-level convenience copies."""

    expected_run_dir = paths.output_dir / "runs" / _safe_artifact_name(
        str(report.run.get("run_id") or "run")
    )
    if paths.run_dir != expected_run_dir:
        raise ValueError("run artifact paths do not match report run_id")
    paths.writeback_json.write_text(
        json.dumps(
            to_jsonable(report.writeback),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paths.writeback_journal.write_text(
        json.dumps(
            to_jsonable(report.audit.get("writeback_journal", {})),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paths.audit_json.write_text(
        json.dumps(
            to_jsonable(report.audit),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paths.final_json.write_text(report_to_json(report), encoding="utf-8")
    paths.final_markdown.write_text(render_markdown(report), encoding="utf-8")
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.top_level_json.write_text(report_to_json(report), encoding="utf-8")
    paths.top_level_markdown.write_text(render_markdown(report), encoding="utf-8")
    write_audit_artifacts(report, paths.output_dir)


def report_to_json(report: OptimizationReport) -> str:
    return json.dumps(to_jsonable(report), indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"


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
        "Update source prompt: "
        + ("yes" if report.run.get("update_source") else "no (default)"),
        "",
    ])
    availability = report.audit.get("sdk_result_availability", {})
    lines.extend([
        "Fake and SDK modes perform complete AgentEvaluator-compatible reevaluation for baseline "
        "and every candidate on both train and validation; optimizer aggregates are never used "
        "as gate evidence.",
        "",
    ])
    if report.run.get("mode") == "sdk":
        lines.extend([
            "SDK availability: "
            f"aggregate_validation_result={availability.get('aggregate_validation_result')}, "
            f"full_train_eval_result={availability.get('full_train_eval_result')}, "
            f"full_per_case_validation_delta={availability.get('full_per_case_validation_delta')}.",
            "",
        ])
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
        "| candidate | split | case | baseline | candidate | delta | passed -> passed | delta type |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ])
    for delta in report.per_case_deltas:
        lines.append(
            f"| {delta.candidate_id} | {delta.split} | {delta.case_id} | "
            f"{delta.baseline_score:.3f} | {delta.candidate_score:.3f} | "
            f"{delta.delta:+.3f} | {delta.baseline_passed} -> {delta.candidate_passed} | "
            f"{delta.delta_type} |"
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
    if summary.get("attribution_accuracy") is not None:
        lines.append("")
        lines.append(f"Attribution accuracy: {summary['attribution_accuracy']:.3f}")

    lines.extend([
        "",
        "## Cost And Audit",
        "",
        f"Total cost: {report.audit.get('cost', {}).get('total', 0):.3f}",
        f"Config hash: `{report.audit.get('config_hash', '')}`",
        f"Run id: `{report.run.get('run_id', '')}`",
    ])

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
        report.run.get("reproducibility_command")
        or report.audit.get("reproducibility_command")
        or REPRODUCIBILITY_COMMAND,
        "```",
        "",
    ])
    return "\n".join(lines)


def write_audit_artifacts(report: OptimizationReport, output_path: Path) -> None:
    run_id = _safe_artifact_name(str(report.run.get("run_id") or "run"))
    run_dir = output_path / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    input_paths = report.audit.get("input_paths", {})
    config_path = input_paths.get("optimizer")
    if config_path and Path(config_path).is_file():
        shutil.copyfile(config_path, run_dir / "config.snapshot.json")
    else:
        (run_dir / "config.snapshot.json").write_text("{}", encoding="utf-8")

    (run_dir / "input_hashes.json").write_text(
        json.dumps(report.audit.get("input_hashes", {}), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    prompt_dir = run_dir / "candidate_prompts"
    results_dir = run_dir / "case_results"
    diffs_dir = run_dir / "prompt_diffs"
    prompt_dir.mkdir(exist_ok=True)
    results_dir.mkdir(exist_ok=True)
    diffs_dir.mkdir(exist_ok=True)

    for record in report.candidates:
        candidate: CandidatePrompt = record["candidate"]
        candidate_name = _safe_artifact_name(candidate.candidate_id)
        candidate_dir = prompt_dir / candidate_name
        candidate_dir.mkdir(exist_ok=True)
        for field_name, prompt_text in candidate.bundle().items():
            field_artifact = _safe_artifact_name(str(field_name))
            (candidate_dir / f"{field_artifact}.txt").write_text(
                prompt_text,
                encoding="utf-8",
            )
        (diffs_dir / f"{candidate_name}.diff").write_text(candidate.prompt_diff, encoding="utf-8")
        for split_name in ("train_result", "validation_result"):
            split_result = record[split_name]
            split_artifact = _safe_artifact_name(str(split_result.split))
            path = results_dir / f"{candidate_name}_{split_artifact}.json"
            path.write_text(
                json.dumps(to_jsonable(split_result), indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )


def _delta_type(*, baseline_passed: bool, candidate_passed: bool, delta: float) -> str:
    if not baseline_passed and candidate_passed:
        return "new_pass"
    if baseline_passed and not candidate_passed:
        return "new_fail"
    if delta > 0:
        return "score_up"
    if delta < 0:
        return "score_down"
    return "unchanged"


def _safe_artifact_name(name: str) -> str:
    if name in {"", ".", ".."} or not ARTIFACT_NAME_RE.fullmatch(name):
        raise ValueError(f"unsafe audit artifact name: {name!r}")
    return name
