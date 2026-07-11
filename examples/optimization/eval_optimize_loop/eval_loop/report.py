"""Report construction and rendering."""

from __future__ import annotations

import ctypes
import errno
import json
import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Callable
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
    run_id: str
    temp_run_dir: Path
    final_run_dir: Path

    @property
    def run_dir(self) -> Path:
        """Return the immutable authoritative run location."""

        return self.final_run_dir

    @property
    def prewrite_json(self) -> Path:
        return self.final_run_dir / "pre_write_report.json"

    @property
    def prewrite_markdown(self) -> Path:
        return self.final_run_dir / "pre_write_report.md"

    @property
    def audit_json(self) -> Path:
        return self.final_run_dir / "audit.json"

    @property
    def final_json(self) -> Path:
        return self.final_run_dir / "optimization_report.json"

    @property
    def final_markdown(self) -> Path:
        return self.final_run_dir / "optimization_report.md"

    @property
    def writeback_json(self) -> Path:
        return self.final_run_dir / "writeback.json"

    @property
    def writeback_journal(self) -> Path:
        return self.final_run_dir / "writeback_journal.json"

    @property
    def top_level_json(self) -> Path:
        return self.output_dir / "optimization_report.json"

    @property
    def top_level_markdown(self) -> Path:
        return self.output_dir / "optimization_report.md"

    def temp_path(self, relative_path: str | Path) -> Path:
        return self.temp_run_dir / relative_path


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
    baseline_prompts: dict[str, str] | None = None,
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
        schema_version="eval_optimize_loop.v2",
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
        baseline_prompts=dict(baseline_prompts or {}),
        rounds=list(rounds or []),
        cost_summary=cost_summary or CostSummary(),
        writeback=writeback or WritebackResult(status="not_requested"),
    )


def validate_unique_round_ids(rounds: list[OptimizationRound]) -> None:
    """Reject round collections that cannot map one-to-one to audit files."""

    seen: set[int] = set()
    for round_record in rounds:
        round_id = round_record.round_id
        if round_id in seen:
            raise ValueError(f"duplicate round_id in optimization report: {round_id}")
        seen.add(round_id)


def write_reports(report: OptimizationReport, output_dir: str | Path) -> tuple[Path, Path]:
    """Compatibility helper for callers that do not perform source writeback."""

    run_id = _safe_artifact_name(str(report.run.get("run_id") or "run"))
    paths = reserve_run_artifacts(output_dir, run_id=run_id)
    prepare_run_artifacts(report, paths)
    finalize_run_artifacts(report, paths)
    return paths.top_level_json, paths.top_level_markdown


def reserve_run_artifacts(
    output_dir: str | Path,
    *,
    run_id: str,
) -> RunArtifactPaths:
    """Exclusively reserve one mutable temp run without touching a final run."""

    safe_run_id = _safe_artifact_name(str(run_id))
    output_path = Path(output_dir)
    runs_root = output_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    if runs_root.is_symlink() or _is_reparse_point(runs_root):
        raise OSError(f"refusing to reserve run artifacts through unsafe runs directory: {runs_root}")

    temp_run_dir = runs_root / f".{safe_run_id}.tmp"
    final_run_dir = runs_root / safe_run_id
    for occupied in (final_run_dir, temp_run_dir):
        if os.path.lexists(occupied):
            raise FileExistsError(
                f"run ID {safe_run_id!r} is already reserved or published in {runs_root}"
            )
    try:
        temp_run_dir.mkdir()
    except FileExistsError as error:
        raise FileExistsError(
            f"run ID {safe_run_id!r} is already reserved or published in {runs_root}"
        ) from error

    # A concurrently published final must win.  Leave no misleading reservation
    # when our just-created directory is still empty.
    if os.path.lexists(final_run_dir):
        try:
            temp_run_dir.rmdir()
        except OSError:
            pass
        raise FileExistsError(
            f"run ID {safe_run_id!r} is already reserved or published in {runs_root}"
        )
    return RunArtifactPaths(
        output_dir=output_path,
        run_id=safe_run_id,
        temp_run_dir=temp_run_dir,
        final_run_dir=final_run_dir,
    )


def prepare_run_artifacts(
    report: OptimizationReport,
    paths_or_output_dir: RunArtifactPaths | str | Path,
) -> RunArtifactPaths:
    """Persist a complete pre-write audit before any source prompt commit."""

    report_run_id = _safe_artifact_name(str(report.run.get("run_id") or "run"))
    if isinstance(paths_or_output_dir, RunArtifactPaths):
        paths = paths_or_output_dir
    else:
        # Legacy direct callers remain safe: they receive a new exclusive
        # reservation and can never reopen an existing temp or final run.
        paths = reserve_run_artifacts(paths_or_output_dir, run_id=report_run_id)
    validate_unique_round_ids(report.rounds)
    _validate_run_artifact_paths(paths, report_run_id=report_run_id)
    _require_mutable_temp_run(paths)

    prewrite_json = report_to_json(report)
    prewrite_markdown = render_markdown(report)
    audit_json = _json_text(report.audit)
    journal = dict(report.audit.get("writeback_journal", {}))
    if journal.get("state") == "pending":
        journal["state"] = "prepared"
    journal_json = _json_text(journal)

    _atomic_write_text(paths.temp_path("pre_write_report.json"), prewrite_json)
    _atomic_write_text(paths.temp_path("pre_write_report.md"), prewrite_markdown)
    _atomic_write_text(paths.temp_path("audit.json"), audit_json)
    write_audit_artifacts(report, paths.temp_run_dir)
    _write_artifact_manifest(report, paths, expected_journal=journal_json)
    # This durable journal is the completion marker for the whole prepare phase,
    # so it must be replaced only after every prerequisite artifact exists.
    persist_writeback_journal(paths, journal)
    return paths


def finalize_run_artifacts(
    report: OptimizationReport,
    paths: RunArtifactPaths,
    *,
    before_publish: Callable[[], None] | None = None,
) -> None:
    """Complete the temp run, publish it once, then refresh convenience copies."""

    report_run_id = _safe_artifact_name(str(report.run.get("run_id") or "run"))
    validate_unique_round_ids(report.rounds)
    _validate_run_artifact_paths(paths, report_run_id=report_run_id)
    _require_mutable_temp_run(paths)

    # Serialize everything before the first final-phase write so non-finite or
    # otherwise invalid values cannot create a misleading terminal artifact set.
    audit_json = _json_text(report.audit)
    final_json = report_to_json(report)
    final_markdown = render_markdown(report)

    # The terminal outcome is authoritative within the still-mutable temp run.
    persist_writeback_outcome(report, paths)
    _atomic_write_text(paths.temp_path("audit.json"), audit_json)
    _atomic_write_text(paths.temp_path("optimization_report.json"), final_json)
    _atomic_write_text(paths.temp_path("optimization_report.md"), final_markdown)
    write_audit_artifacts(report, paths.temp_run_dir)
    _write_artifact_manifest(report, paths)

    # This is intentionally the last callback before the one-way directory
    # rename.  It may downgrade the temp journal and raise, but publication makes
    # every final-run byte immutable to this lifecycle.
    if before_publish is not None:
        try:
            before_publish()
        except BaseException as callback_error:
            try:
                _write_artifact_manifest(report, paths)
            except BaseException as manifest_error:
                add_note = getattr(callback_error, "add_note", None)
                if add_note is not None:
                    add_note(
                        "failed to refresh temp artifact manifest after before_publish failure: "
                        f"{manifest_error}"
                    )
            raise
    _durable_publish_directory(paths)

    # Convenience copies are non-authoritative and are refreshed only from the
    # already-published immutable run.  Failure here leaves that final intact.
    _atomic_write_bytes(paths.top_level_json, paths.final_json.read_bytes())
    _atomic_write_bytes(paths.top_level_markdown, paths.final_markdown.read_bytes())


def persist_writeback_journal(paths: RunArtifactPaths, journal: dict[str, Any]) -> None:
    """Atomically persist the authoritative writeback state machine record."""

    _require_mutable_temp_run(paths)
    _atomic_write_text(paths.temp_path("writeback_journal.json"), _json_text(journal))


def persist_writeback_outcome(report: OptimizationReport, paths: RunArtifactPaths) -> None:
    """Persist a terminal writeback outcome before any nonessential artifact."""

    persist_writeback_journal(paths, dict(report.audit.get("writeback_journal", {})))
    _atomic_write_text(paths.temp_path("writeback.json"), _json_text(report.writeback))


def _validate_run_artifact_paths(paths: RunArtifactPaths, *, report_run_id: str) -> None:
    runs_root = paths.output_dir / "runs"
    if paths.run_id != report_run_id:
        raise ValueError("run artifact paths do not match report run_id")
    if paths.temp_run_dir != runs_root / f".{report_run_id}.tmp":
        raise ValueError("run artifact temp path does not match report run_id")
    if paths.final_run_dir != runs_root / report_run_id:
        raise ValueError("run artifact final path does not match report run_id")


def _require_mutable_temp_run(paths: RunArtifactPaths) -> None:
    if os.path.lexists(paths.final_run_dir):
        raise FileExistsError(f"run ID {paths.run_id!r} is already published")
    try:
        metadata = os.lstat(paths.temp_run_dir)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"reserved temp directory for run ID {paths.run_id!r} is unavailable"
        ) from error
    if paths.temp_run_dir.is_symlink() or _is_reparse_point(paths.temp_run_dir):
        raise OSError(f"refusing unsafe reserved temp directory: {paths.temp_run_dir}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"reserved temp path is not a directory: {paths.temp_run_dir}")


def report_to_json(report: OptimizationReport) -> str:
    validate_unique_round_ids(report.rounds)
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


def _durable_publish_directory(paths: RunArtifactPaths) -> None:
    """Atomically move the reserved temp run to a never-replaced final path."""

    _require_mutable_temp_run(paths)
    source = paths.temp_run_dir
    target = paths.final_run_dir
    if target.is_symlink() or _is_reparse_point(target):
        raise OSError(f"refusing to publish over unsafe/reparse target: {target}")
    if os.path.lexists(target):
        raise FileExistsError(f"run ID {paths.run_id!r} is already published")

    if os.name == "nt":
        move_file_ex = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move_file_ex.restype = ctypes.c_int
        movefile_write_through = 0x00000008
        succeeded = move_file_ex(
            os.path.abspath(source),
            os.path.abspath(target),
            movefile_write_through,
        )
        if not succeeded:
            error_code = ctypes.get_last_error()
            if os.path.lexists(target):
                raise FileExistsError(
                    error_code,
                    f"run ID {paths.run_id!r} is already published",
                    str(target),
                )
            raise ctypes.WinError(error_code)
        return

    _posix_rename_noreplace(source, target)
    _fsync_directory(target.parent)


def _posix_rename_noreplace(source: Path, target: Path) -> None:
    """Atomically rename a Linux directory without replacing any target."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as error:
        raise OSError(
            errno.ENOTSUP,
            "atomic POSIX no-replace directory rename is unavailable",
            str(target),
        ) from error

    at_fdcwd = -100
    rename_noreplace = 0x1
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(target),
        rename_noreplace,
    )
    if result == 0:
        return

    error_number = ctypes.get_errno() or errno.EIO
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            error_number,
            os.strerror(error_number),
            str(target),
        )
    raise OSError(error_number, os.strerror(error_number), str(target))


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def render_markdown(report: OptimizationReport) -> str:
    validate_unique_round_ids(report.rounds)
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


def write_audit_artifacts(report: OptimizationReport, run_dir: Path) -> None:
    """Write the complete structured audit set inside a mutable run root."""

    validate_unique_round_ids(report.rounds)
    run_dir = Path(run_dir)
    _require_safe_audit_root(run_dir)

    for component in (
        "case_results",
        "baseline_prompts",
        "candidate_prompts",
        "prompt_diffs",
        "rounds",
    ):
        _ensure_safe_audit_directory(run_dir, component)
    for index, record in enumerate(report.candidates, start=1):
        candidate: CandidatePrompt = record["candidate"]
        _ensure_safe_audit_directory(
            run_dir,
            "candidate_prompts",
            _candidate_artifact_name(index, candidate.candidate_id),
        )

    # Never copy the raw optimizer file: it may contain API keys.  The original
    # byte hash remains in input_hashes while this human-readable snapshot is redacted.
    _write_safe_audit_text(
        run_dir,
        "config.snapshot.json",
        _json_text(report.audit.get("config_snapshot", {})),
    )
    _write_safe_audit_text(
        run_dir,
        "input_hashes.json",
        _json_text(report.audit.get("input_hashes", {})),
    )

    for field_name, prompt_text in report.baseline_prompts.items():
        field_artifact = _safe_artifact_name(str(field_name))
        _write_safe_audit_text(
            run_dir,
            Path("baseline_prompts") / f"{field_artifact}.txt",
            prompt_text,
        )

    _write_safe_audit_text(
        run_dir,
        Path("case_results") / "baseline_train.json",
        _json_text(report.baseline_train),
    )
    _write_safe_audit_text(
        run_dir,
        Path("case_results") / "baseline_validation.json",
        _json_text(report.baseline_validation),
    )
    _write_safe_audit_text(run_dir, "rounds.json", _json_text(report.rounds))
    for round_record in report.rounds:
        round_artifact = _safe_artifact_name(str(round_record.round_id))
        _write_safe_audit_text(
            run_dir,
            Path("rounds") / f"{round_artifact}.json",
            _json_text(round_record),
        )
    _write_safe_audit_text(
        run_dir,
        "per_case_deltas.json",
        _json_text(report.per_case_deltas),
    )
    _write_safe_audit_text(
        run_dir,
        "gate_decisions.json",
        _json_text(report.gate_decisions),
    )
    _write_safe_audit_text(
        run_dir,
        "evaluation_failures.json",
        _json_text(report.audit.get("candidate_evaluation_failures", {})),
    )
    _write_safe_audit_text(run_dir, "writeback.json", _json_text(report.writeback))

    for index, record in enumerate(report.candidates, start=1):
        candidate: CandidatePrompt = record["candidate"]
        candidate_name = _candidate_artifact_name(index, candidate.candidate_id)
        prompt_bundle = record.get("prompt_bundle")
        if prompt_bundle is None:
            prompt_bundle = candidate.bundle()
        for field_name, prompt_text in prompt_bundle.items():
            field_artifact = _safe_artifact_name(str(field_name))
            _write_safe_audit_text(
                run_dir,
                Path("candidate_prompts") / candidate_name / f"{field_artifact}.txt",
                prompt_text,
            )
        _write_safe_audit_text(
            run_dir,
            Path("prompt_diffs") / f"{candidate_name}.diff",
            candidate.prompt_diff,
        )
        for split_name in ("train_result", "validation_result"):
            split_result = record.get(split_name)
            if not isinstance(split_result, EvalResult):
                continue
            split_artifact = _safe_artifact_name(str(split_result.split))
            _write_safe_audit_text(
                run_dir,
                Path("case_results") / f"{candidate_name}_{split_artifact}.json",
                _json_text(split_result),
            )


def _require_safe_audit_root(run_root: Path) -> None:
    run_root = Path(run_root)
    try:
        metadata = os.lstat(run_root)
    except FileNotFoundError as error:
        raise OSError(f"audit run root is unavailable: {run_root}") from error
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(run_root):
        raise OSError(f"audit run root is unsafe/reparse: {run_root}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"audit run root is not a directory: {run_root}")


def _write_safe_audit_text(
    run_root: Path,
    relative_path: str | Path,
    content: str,
) -> None:
    """Recheck an audit file's root and parent immediately before writing."""

    run_root = Path(run_root)
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(component in {"", ".", ".."} for component in relative.parts)
    ):
        raise OSError(f"unsafe audit artifact path: {relative_path!r}")
    if len(relative.parts) == 1:
        _require_safe_audit_root(run_root)
        parent = run_root
    else:
        parent = _ensure_safe_audit_directory(run_root, *relative.parts[:-1])
    target = parent / relative.parts[-1]
    try:
        target.relative_to(run_root)
    except ValueError as error:
        raise OSError(f"audit artifact escapes reserved run root: {target}") from error
    _atomic_write_text(target, content)


def _ensure_safe_audit_directory(run_root: Path, *components: str) -> Path:
    """Return a checked audit directory rooted lexically beneath ``run_root``.

    Every existing component is inspected without following links. Missing
    components are created one level at a time and immediately re-inspected.
    Callers use the returned directory directly for the following writes; this
    eliminates deterministic escapes left by a backend, but does not claim a
    portable cross-process no-follow guarantee after the final inspection.
    """

    run_root = Path(run_root)
    _require_safe_audit_root(run_root)
    if not components:
        raise ValueError("audit directory components must not be empty")
    for component in components:
        component_path = Path(component)
        if (
            not component
            or component_path.is_absolute()
            or len(component_path.parts) != 1
            or component_path.name != component
            or component in {".", ".."}
        ):
            raise OSError(f"unsafe audit directory component: {component!r}")

    directory = run_root.joinpath(*components)
    try:
        directory.relative_to(run_root)
    except ValueError as error:
        raise OSError(f"audit directory escapes reserved run root: {directory}") from error

    current = run_root
    for component in components:
        current = current / component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current)
            except FileExistsError:
                # A racing creator must pass the same no-follow inspection.
                pass
            metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(current):
            raise OSError(f"refusing unsafe/reparse audit directory: {current}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError(f"audit path component is not a directory: {current}")
    return directory


def _write_artifact_manifest(
    report: OptimizationReport,
    paths: RunArtifactPaths,
    *,
    expected_journal: str | None = None,
) -> None:
    _require_mutable_temp_run(paths)
    artifacts = _manifest_artifact_layout(report)
    expected_files = (
        {"writeback_journal.json": expected_journal.encode("utf-8")}
        if expected_journal is not None
        else {}
    )
    files = _manifest_file_records(paths.temp_run_dir, expected_files=expected_files)
    declared_paths = _declared_manifest_paths(artifacts)
    if expected_journal is not None:
        declared_paths -= {"optimization_report.json", "optimization_report.md"}
    available_paths = {item["path"] for item in files}
    missing = declared_paths - available_paths
    if missing:
        raise OSError(f"audit artifact manifest is incomplete: missing {sorted(missing)}")
    manifest = {
        "schema_version": "eval_optimize_loop.artifacts.v1",
        "run_id": paths.run_id,
        "artifacts": artifacts,
        "files": files,
    }
    _write_safe_audit_text(
        paths.temp_run_dir,
        "artifact_manifest.json",
        _json_text(manifest),
    )


def _manifest_artifact_layout(report: OptimizationReport) -> dict[str, Any]:
    validate_unique_round_ids(report.rounds)
    candidates: list[dict[str, Any]] = []
    for index, record in enumerate(report.candidates, start=1):
        candidate: CandidatePrompt = record["candidate"]
        artifact_id = _candidate_artifact_name(index, candidate.candidate_id)
        prompt_bundle = record.get("prompt_bundle") or candidate.bundle()
        prompt_paths = {
            field_name: (
                Path("candidate_prompts")
                / artifact_id
                / f"{_safe_artifact_name(str(field_name))}.txt"
            ).as_posix()
            for field_name in prompt_bundle
        }
        case_results: dict[str, str] = {}
        for result_name in ("train_result", "validation_result"):
            result = record.get(result_name)
            if not isinstance(result, EvalResult):
                continue
            split = _safe_artifact_name(str(result.split))
            case_results[str(result.split)] = (
                Path("case_results") / f"{artifact_id}_{split}.json"
            ).as_posix()
        candidates.append(
            {
                "candidate_id": candidate.candidate_id,
                "artifact_id": artifact_id,
                "prompt_bundle": prompt_paths,
                "diff": (Path("prompt_diffs") / f"{artifact_id}.diff").as_posix(),
                "case_results": case_results,
                "evaluation_failure": "evaluation_error" in record,
            }
        )

    return {
        "reports": {
            "prewrite_json": "pre_write_report.json",
            "prewrite_markdown": "pre_write_report.md",
            "final_json": "optimization_report.json",
            "final_markdown": "optimization_report.md",
        },
        "baseline_case_results": {
            "train": "case_results/baseline_train.json",
            "validation": "case_results/baseline_validation.json",
        },
        "baseline_prompts": {
            field_name: (
                Path("baseline_prompts") / f"{_safe_artifact_name(str(field_name))}.txt"
            ).as_posix()
            for field_name in report.baseline_prompts
        },
        "rounds": [
            (Path("rounds") / f"{_safe_artifact_name(str(round_record.round_id))}.json").as_posix()
            for round_record in report.rounds
        ],
        "audit_records": {
            "audit": "audit.json",
            "config_snapshot": "config.snapshot.json",
            "input_hashes": "input_hashes.json",
            "rounds": "rounds.json",
            "per_case_deltas": "per_case_deltas.json",
            "gate_decisions": "gate_decisions.json",
            "writeback": "writeback.json",
            "writeback_journal": "writeback_journal.json",
            "evaluation_failures": "evaluation_failures.json",
        },
        "candidates": candidates,
    }


def _declared_manifest_paths(artifacts: dict[str, Any]) -> set[str]:
    declared: set[str] = set()
    for group_name in ("reports", "baseline_case_results", "baseline_prompts", "audit_records"):
        declared.update(artifacts[group_name].values())
    declared.update(artifacts["rounds"])
    for candidate in artifacts["candidates"]:
        declared.add(candidate["diff"])
        declared.update(candidate["prompt_bundle"].values())
        declared.update(candidate["case_results"].values())
    return declared


def _manifest_file_records(
    run_dir: Path,
    *,
    expected_files: dict[str, bytes],
) -> list[dict[str, Any]]:
    content_by_path: dict[str, bytes] = dict(expected_files)
    for artifact in run_dir.rglob("*"):
        if artifact.is_symlink() or _is_reparse_point(artifact):
            raise OSError(f"refusing unsafe/reparse audit artifact: {artifact}")
        if not artifact.is_file():
            continue
        relative_path = artifact.relative_to(run_dir).as_posix()
        if relative_path == "artifact_manifest.json":
            continue
        content_by_path[relative_path] = artifact.read_bytes()
    return [
        {
            "path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
        for relative_path, content in sorted(content_by_path.items())
    ]


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
