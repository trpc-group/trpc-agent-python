"""Report construction and rendering."""

from __future__ import annotations

import ctypes
import json
import hashlib
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .attribution import summarize_failures
from .artifacts import validate_artifact_component
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
        for result_name in ("train_result", "validation_result"):
            result = record.get(result_name)
            if isinstance(result, EvalResult):
                all_results.append(result)
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
    _atomic_write_text(paths.prewrite_json, report_to_json(report))
    _atomic_write_text(paths.prewrite_markdown, render_markdown(report))
    _atomic_write_text(paths.audit_json, _json_text(report.audit))
    write_audit_artifacts(report, output_path)
    journal = dict(report.audit.get("writeback_journal", {}))
    if journal.get("state") == "pending":
        journal["state"] = "prepared"
    # This durable journal is the completion marker for the whole prepare phase,
    # so it must be replaced only after every prerequisite artifact exists.
    persist_writeback_journal(paths, journal)
    return paths


def finalize_run_artifacts(report: OptimizationReport, paths: RunArtifactPaths) -> None:
    """Persist final writeback state and refresh top-level convenience copies."""

    expected_run_dir = paths.output_dir / "runs" / _safe_artifact_name(
        str(report.run.get("run_id") or "run")
    )
    if paths.run_dir != expected_run_dir:
        raise ValueError("run artifact paths do not match report run_id")
    # The terminal outcome is authoritative and is persisted before convenience
    # reports.  A later rendering failure cannot erase knowledge of source state.
    persist_writeback_outcome(report, paths)
    _atomic_write_text(paths.audit_json, _json_text(report.audit))
    _atomic_write_text(paths.final_json, report_to_json(report))
    _atomic_write_text(paths.final_markdown, render_markdown(report))
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(paths.top_level_json, report_to_json(report))
    _atomic_write_text(paths.top_level_markdown, render_markdown(report))
    write_audit_artifacts(report, paths.output_dir)


def persist_writeback_journal(paths: RunArtifactPaths, journal: dict[str, Any]) -> None:
    """Atomically persist the authoritative writeback state machine record."""

    _atomic_write_text(paths.writeback_journal, _json_text(journal))


def persist_writeback_outcome(report: OptimizationReport, paths: RunArtifactPaths) -> None:
    """Persist a terminal writeback outcome before any nonessential artifact."""

    persist_writeback_journal(paths, dict(report.audit.get("writeback_journal", {})))
    _atomic_write_text(paths.writeback_json, _json_text(report.writeback))


def report_to_json(report: OptimizationReport) -> str:
    return json.dumps(to_jsonable(report), indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"


def _json_text(value: Any) -> str:
    return (
        json.dumps(
            to_jsonable(value),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Durably replace a critical artifact without exposing a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        stream = os.fdopen(fd, "wb")
        fd = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _durable_replace(temp_path, path)
    finally:
        active_exception = sys.exc_info()[0] is not None
        cleanup_error: OSError | None = None
        if fd >= 0:
            try:
                os.close(fd)
            except OSError as error:
                cleanup_error = error
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            if cleanup_error is None:
                cleanup_error = error
        if cleanup_error is not None and not active_exception:
            raise cleanup_error


def _fsync_directory(directory: Path) -> None:
    """Synchronize a POSIX directory entry after atomic replacement."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _durable_replace(source: Path, target: Path) -> None:
    """Atomically replace a file and wait for rename metadata to reach storage."""

    if os.name == "nt":
        if _is_reparse_point(target):
            raise OSError(f"refusing to replace reparse-point target: {target}")
        move_file_ex = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move_file_ex.restype = ctypes.c_int
        movefile_replace_existing = 0x00000001
        movefile_write_through = 0x00000008
        succeeded = move_file_ex(
            os.path.abspath(source),
            os.path.abspath(target),
            movefile_replace_existing | movefile_write_through,
        )
        if not succeeded:
            raise ctypes.WinError(ctypes.get_last_error())
        return
    os.replace(source, target)
    _fsync_directory(target.parent)


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


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
    if report.audit.get("candidate_evaluation_failures"):
        lines.extend([
            "Fake and SDK modes require complete AgentEvaluator-compatible train and validation "
            "reevaluation before gating a candidate; evaluation errors are recorded as explicit "
            "rejections and optimizer aggregates are never used as gate evidence.",
            "",
        ])
    else:
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
        train_result = record.get("train_result")
        validation_result = record.get("validation_result")
        train_score = f"{train_result.score:.3f}" if isinstance(train_result, EvalResult) else "n/a"
        validation_score = (
            f"{validation_result.score:.3f}"
            if isinstance(validation_result, EvalResult)
            else "n/a"
        )
        lines.append(
            f"| {candidate.candidate_id} | {train_score} | "
            f"{validation_score} | {verdict} |"
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

    cost_audit = report.audit.get("cost", {})
    lines.extend(["", "## Cost And Audit", ""])
    if cost_audit.get("complete"):
        lines.append(f"Total cost: {cost_audit.get('total', 0):.3f}")
    else:
        reported_optimizer_cost = cost_audit.get("reported_optimizer_cost")
        if reported_optimizer_cost is not None:
            lines.append(
                "Reported optimizer cost (incomplete; not total run cost): "
                f"{reported_optimizer_cost:.3f}"
            )
        lines.append(f"Known evaluator cost: {cost_audit.get('evaluator', 0):.3f}")
        lines.append(
            "Known run cost (incomplete; not total run cost): "
            f"{cost_audit.get('known_run_cost', 0):.3f}"
        )
        lines.append("Complete run cost: unavailable")
    lines.extend([
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
        f"```{report.run.get('reproducibility_shell') or 'bash'}",
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

    # Never copy the raw optimizer file: it may contain API keys.  The original
    # byte hash remains in input_hashes while this human-readable snapshot is redacted.
    _atomic_write_text(
        run_dir / "config.snapshot.json",
        _json_text(report.audit.get("config_snapshot", {})),
    )
    _atomic_write_text(
        run_dir / "input_hashes.json",
        _json_text(report.audit.get("input_hashes", {})),
    )

    prompt_dir = run_dir / "candidate_prompts"
    results_dir = run_dir / "case_results"
    diffs_dir = run_dir / "prompt_diffs"
    prompt_dir.mkdir(exist_ok=True)
    results_dir.mkdir(exist_ok=True)
    diffs_dir.mkdir(exist_ok=True)

    for index, record in enumerate(report.candidates, start=1):
        candidate: CandidatePrompt = record["candidate"]
        candidate_name = _candidate_artifact_name(index, candidate.candidate_id)
        candidate_dir = prompt_dir / candidate_name
        candidate_dir.mkdir(exist_ok=True)
        prompt_bundle = record.get("prompt_bundle")
        if prompt_bundle is None:
            prompt_bundle = candidate.bundle()
        for field_name, prompt_text in prompt_bundle.items():
            field_artifact = _safe_artifact_name(str(field_name))
            _atomic_write_text(candidate_dir / f"{field_artifact}.txt", prompt_text)
        _atomic_write_text(diffs_dir / f"{candidate_name}.diff", candidate.prompt_diff)
        for split_name in ("train_result", "validation_result"):
            split_result = record.get(split_name)
            if not isinstance(split_result, EvalResult):
                continue
            split_artifact = _safe_artifact_name(str(split_result.split))
            path = results_dir / f"{candidate_name}_{split_artifact}.json"
            _atomic_write_text(path, _json_text(split_result))


def _candidate_artifact_name(index: int, candidate_id: str) -> str:
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:12]
    return f"{index:03d}-{digest}"


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
    return validate_artifact_component(name, context="audit artifact name")
