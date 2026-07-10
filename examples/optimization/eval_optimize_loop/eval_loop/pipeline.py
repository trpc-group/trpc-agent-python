"""Backend-neutral asynchronous evaluation and optimization orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .attribution import summarize_failures
from .artifacts import validate_artifact_component
from .backends import EvaluationBackend
from .backends import OptimizationBackend
from .config import _parse_gate_config
from .config import parse_optimizer_config
from .config import resolve_effective_seed
from .gate import AcceptanceGate
from .loader import read_json
from .loader import stable_config_hash
from .report import build_report
from .report import compute_case_deltas
from .report import finalize_run_artifacts
from .report import persist_writeback_journal
from .report import persist_writeback_outcome
from .report import prepare_run_artifacts
from .schemas import CandidatePrompt
from .schemas import CostSummary
from .schemas import EvalResult
from .schemas import GateDecision
from .schemas import OptimizationReport
from .schemas import OptimizationResult
from .schemas import WritebackResult
from .schemas import to_jsonable
from .writeback import commit_prompt_bundle
from .writeback import ConcurrentPromptUpdateError
from .writeback import PromptRestorationError
from .writeback import snapshot_prompt_files

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class PipelineRequest:
    """All inputs required by the shared asynchronous pipeline."""

    train_path: str | Path
    validation_path: str | Path
    optimizer_config_path: str | Path
    output_dir: str | Path
    target_prompt_paths: dict[str, str | Path]
    gate_config: dict[str, Any]
    trace: bool
    update_source: bool
    mode: str
    run_id: str
    sdk_call_agent: str | None = None
    gate_config_path: str | Path | None = None
    effective_seed: int | None = None
    gate_config_source: str = "request"


@dataclass(frozen=True)
class _InputFileSnapshot:
    role: str
    original_path: Path
    snapshot_path: Path
    sha256: str


@dataclass(frozen=True)
class _InputSnapshots:
    files: dict[str, _InputFileSnapshot]

    def path(self, role: str) -> Path:
        return self.files[role].snapshot_path

    def hashes(self) -> dict[str, str]:
        return {role: item.sha256 for role, item in self.files.items()}

    def cleanup(self) -> None:
        failures: list[str] = []
        for item in self.files.values():
            try:
                item.snapshot_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                failures.append(f"{item.role}: {error}")
        if failures:
            raise OSError("failed to remove input snapshots: " + "; ".join(failures))


async def execute_pipeline(
    request: PipelineRequest,
    *,
    evaluator: EvaluationBackend,
    optimizer: OptimizationBackend,
) -> OptimizationReport:
    """Evaluate, optimize, gate, audit, and optionally commit one prompt bundle."""

    started = time.perf_counter()
    run_id = _safe_artifact_component(request.run_id, context="run_id")
    if not request.target_prompt_paths:
        raise ValueError("target_prompt_paths must not be empty")
    _validate_target_prompt_fields(request.target_prompt_paths)
    if Path(request.train_path).resolve() == Path(request.validation_path).resolve():
        raise ValueError("train and validation evalset paths must be different")
    run_root = _claim_run_directory(request.output_dir, run_id=run_id)

    input_snapshots = _snapshot_pipeline_inputs(request, run_id=run_id)
    try:
        result = await _execute_with_snapshots(
            request,
            evaluator=evaluator,
            optimizer=optimizer,
            started=started,
            run_id=run_id,
            run_root=run_root,
            input_snapshots=input_snapshots,
        )
    except BaseException as primary_error:
        try:
            input_snapshots.cleanup()
        except OSError as cleanup_error:
            add_note = getattr(primary_error, "add_note", None)
            if add_note is not None:
                add_note(str(cleanup_error))
        raise
    else:
        input_snapshots.cleanup()
        return result


def _validate_target_prompt_fields(target_prompt_paths: dict[str, str | Path]) -> None:
    seen: set[str] = set()
    resolved_paths: set[Path] = set()
    for field_name, prompt_path in target_prompt_paths.items():
        try:
            validate_artifact_component(field_name, context="target prompt field")
        except ValueError as error:
            raise ValueError(f"target prompt field {field_name!r} is unsafe") from error

        normalized_name = field_name.casefold()
        if normalized_name in seen:
            raise ValueError("target prompt field names must be case-insensitively unique")
        seen.add(normalized_name)

        resolved_path = Path(prompt_path).resolve()
        if resolved_path in resolved_paths:
            raise ValueError("target prompt fields must not reference the same resolved file")
        resolved_paths.add(resolved_path)


async def _execute_with_snapshots(
    request: PipelineRequest,
    *,
    evaluator: EvaluationBackend,
    optimizer: OptimizationBackend,
    started: float,
    run_id: str,
    run_root: Path,
    input_snapshots: _InputSnapshots,
) -> OptimizationReport:
    prompt_snapshot = snapshot_prompt_files(request.target_prompt_paths)
    baseline_prompts = MappingProxyType(_decode_snapshot(prompt_snapshot))
    raw_config = read_json(input_snapshots.path("optimizer"))
    effective_seed = resolve_effective_seed(
        raw_config,
        path=request.optimizer_config_path,
        strict_legacy=request.mode == "fake",
    )
    if request.effective_seed is not None and request.effective_seed != effective_seed:
        raise ValueError("effective optimizer seed changed between dependency construction and pipeline entry")
    if request.mode == "fake":
        _validate_backend_seed(evaluator, effective_seed)
        if optimizer is not evaluator:
            _validate_backend_seed(optimizer, effective_seed)
    train_metadata = _strict_case_metadata(input_snapshots.path("train"), role="train")
    validation_metadata = _strict_case_metadata(
        input_snapshots.path("validation"),
        role="validation",
    )
    gate_config = _effective_gate_config(request, input_snapshots, raw_config=raw_config)
    effective_gate_config = _gate_config_with_dataset_protection(
        gate_config,
        validation_metadata=validation_metadata,
    )
    train_path = input_snapshots.path("train")
    validation_path = input_snapshots.path("validation")
    optimizer_path = input_snapshots.path("optimizer")
    _verify_snapshot_integrity(input_snapshots)

    _verify_snapshot_integrity(input_snapshots, "train")
    baseline_train = await evaluator.evaluate(
        prompt_id="baseline",
        prompts=dict(baseline_prompts),
        dataset_path=train_path,
        split="train",
        trace=request.trace,
        artifact_dir=_artifact_dir(run_root, "evaluations", "000_baseline", "train"),
    )
    _verify_snapshot_integrity(input_snapshots, "train")
    _validate_eval_result(
        baseline_train,
        expected_prompt_id="baseline",
        expected_split="train",
        expected_case_ids=set(train_metadata),
    )
    _verify_snapshot_integrity(input_snapshots, "validation")
    baseline_validation = await evaluator.evaluate(
        prompt_id="baseline",
        prompts=dict(baseline_prompts),
        dataset_path=validation_path,
        split="validation",
        trace=request.trace,
        artifact_dir=_artifact_dir(run_root, "evaluations", "000_baseline", "validation"),
    )
    _verify_snapshot_integrity(input_snapshots, "validation")
    _validate_eval_result(
        baseline_validation,
        expected_prompt_id="baseline",
        expected_split="validation",
        expected_case_ids=set(validation_metadata),
    )

    failure_summary = summarize_failures([baseline_train])
    _verify_snapshot_integrity(input_snapshots, "train", "validation", "optimizer")
    optimization = await optimizer.optimize_candidates(
        baseline_prompts=dict(baseline_prompts),
        baseline_train=baseline_train,
        failure_summary=failure_summary,
        train_path=train_path,
        validation_path=validation_path,
        config_path=optimizer_path,
        artifact_dir=_artifact_dir(run_root, "optimizer"),
    )
    _verify_snapshot_integrity(input_snapshots, "train", "validation", "optimizer")
    _validate_optimization_result(optimization)
    candidate_bundles = _validated_candidate_bundles(
        optimization.candidates,
        expected_fields=set(prompt_snapshot.files),
    )

    gate = AcceptanceGate(effective_gate_config)
    baseline_evaluator_cost = round(baseline_train.cost + baseline_validation.cost, 6)
    explicit_evaluator_cost = baseline_evaluator_cost
    candidate_records: list[dict[str, Any]] = []
    all_deltas = []
    gate_decisions = []
    all_results_comparable = True
    candidate_evaluation_failed = False

    for index, candidate in enumerate(optimization.candidates, start=1):
        bundle = candidate_bundles[candidate.candidate_id]
        canonical_candidate = CandidatePrompt(
            candidate_id=candidate.candidate_id,
            prompt=candidate.prompt,
            rationale=candidate.rationale,
            prompt_diff=candidate.prompt_diff,
            prompt_fields=dict(bundle),
        )
        path_label = _candidate_artifact_component(index, candidate.candidate_id)
        record: dict[str, Any] = {
            "candidate": canonical_candidate,
            "prompt_bundle": dict(bundle),
        }
        _verify_snapshot_integrity(input_snapshots, "train")
        train_artifact_dir = _artifact_dir(
            run_root,
            "evaluations",
            path_label,
            "train",
        )
        try:
            train_result = await evaluator.evaluate(
                prompt_id=candidate.candidate_id,
                prompts=dict(bundle),
                dataset_path=train_path,
                split="train",
                trace=request.trace,
                artifact_dir=train_artifact_dir,
            )
        except Exception as error:
            record["evaluation_error"] = _recoverable_candidate_evaluation_failure(
                error,
                stage="train",
                completed_results=[],
                input_snapshots=input_snapshots,
                prompt_snapshot=prompt_snapshot,
            )
            candidate_records.append(record)
            candidate_evaluation_failed = True
            all_results_comparable = False
            continue
        _verify_snapshot_integrity(input_snapshots, "train")
        _validate_eval_result(
            train_result,
            expected_prompt_id=candidate.candidate_id,
            expected_split="train",
        )
        record["train_result"] = train_result
        explicit_evaluator_cost = round(explicit_evaluator_cost + train_result.cost, 6)
        _verify_snapshot_integrity(input_snapshots, "validation")
        validation_artifact_dir = _artifact_dir(
            run_root,
            "evaluations",
            path_label,
            "validation",
        )
        try:
            validation_result = await evaluator.evaluate(
                prompt_id=candidate.candidate_id,
                prompts=dict(bundle),
                dataset_path=validation_path,
                split="validation",
                trace=request.trace,
                artifact_dir=validation_artifact_dir,
            )
        except Exception as error:
            record["evaluation_error"] = _recoverable_candidate_evaluation_failure(
                error,
                stage="validation",
                completed_results=[train_result],
                input_snapshots=input_snapshots,
                prompt_snapshot=prompt_snapshot,
            )
            candidate_records.append(record)
            candidate_evaluation_failed = True
            all_results_comparable = False
            continue
        _verify_snapshot_integrity(input_snapshots, "validation")
        _validate_eval_result(
            validation_result,
            expected_prompt_id=candidate.candidate_id,
            expected_split="validation",
        )
        record["validation_result"] = validation_result
        explicit_evaluator_cost = round(explicit_evaluator_cost + validation_result.cost, 6)
        candidate_records.append(record)

    run_cost_complete = optimization.cost.complete and not candidate_evaluation_failed
    gate_cost_summary = replace(optimization.cost, complete=run_cost_complete)
    cumulative_cost = round(baseline_evaluator_cost + optimization.cost.total, 6)
    for record in candidate_records:
        candidate = record["candidate"]
        if "evaluation_error" in record:
            decision = _evaluation_error_gate_decision(
                candidate_id=candidate.candidate_id,
                failure=record["evaluation_error"],
                cumulative_cost=cumulative_cost,
            )
            gate_decisions.append(decision)
            cumulative_cost = decision.total_run_cost
            continue

        train_result = record["train_result"]
        validation_result = record["validation_result"]
        comparable = _result_pair_is_comparable(baseline_train, train_result) and (
            _result_pair_is_comparable(baseline_validation, validation_result)
        )
        all_results_comparable = all_results_comparable and comparable
        deltas = (
            compute_case_deltas(
                candidate_id=candidate.candidate_id,
                baseline_train=baseline_train,
                baseline_validation=baseline_validation,
                candidate_train=train_result,
                candidate_validation=validation_result,
            )
            if comparable
            else []
        )
        decision = gate.decide(
            candidate_id=candidate.candidate_id,
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=train_result,
            candidate_validation=validation_result,
            deltas=deltas,
            cost_summary=gate_cost_summary,
            cumulative_cost=cumulative_cost,
        )
        all_deltas.extend(deltas)
        gate_decisions.append(decision)
        cumulative_cost = decision.total_run_cost

    selected_candidate = _select_candidate(candidate_records, gate_decisions)
    cost_summary = CostSummary(
        optimizer=optimization.cost.optimizer,
        evaluator=round(optimization.cost.evaluator + explicit_evaluator_cost, 6),
        agent=optimization.cost.agent,
        total=cumulative_cost,
        complete=run_cost_complete,
        reported_optimizer_cost=_reported_optimizer_cost(optimization.cost),
    )
    _validate_cost_summary(cost_summary, context="pipeline cost summary")
    input_hashes: dict[str, Any] = {
        **input_snapshots.hashes(),
        "target_prompts": prompt_snapshot.hashes(),
    }
    before_hashes = prompt_snapshot.hashes()
    writeback_journal = _initial_writeback_journal(
        run_id=run_id,
        selected_candidate=selected_candidate,
        update_source=request.update_source,
        before_hashes=before_hashes,
        candidate_hashes=(
            _prompt_bundle_hashes(candidate_bundles[selected_candidate]) if selected_candidate is not None else {}
        ),
        input_hashes=input_snapshots.hashes(),
    )
    audit = _build_audit(
        request=request,
        started=started,
        snapshot_hashes=prompt_snapshot.hashes(),
        input_hashes=input_hashes,
        config_snapshot=_sanitize_public_value(raw_config),
        raw_config_hash=stable_config_hash(raw_config),
        effective_seed=effective_seed,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        candidate_bundles=candidate_bundles,
        optimization_raw_summary=optimization.raw_summary,
        optimization_cost=optimization.cost,
        cost_summary=cost_summary,
        all_results_comparable=all_results_comparable,
        effective_gate_config=effective_gate_config,
        writeback_journal=writeback_journal,
    )
    run = _build_run(request, baseline_train=baseline_train, baseline_validation=baseline_validation)
    if selected_candidate is None:
        provisional_writeback = WritebackResult(status="rejected", before_hashes=before_hashes)
    else:
        provisional_writeback = WritebackResult(
            status="not_requested",
            before_hashes=before_hashes,
            error=(
                "pending source writeback; pre-write audit must be prepared before commit"
                if request.update_source
                else None
            ),
        )
    report = build_report(
        run=run,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        per_case_deltas=all_deltas,
        gate_decisions=gate_decisions,
        selected_candidate=selected_candidate,
        audit=audit,
        rounds=optimization.rounds,
        cost_summary=cost_summary,
        writeback=provisional_writeback,
    )

    artifact_paths = prepare_run_artifacts(report, request.output_dir)
    if selected_candidate is None or not request.update_source:
        terminal_journal = _terminal_journal(
            writeback_journal,
            provisional_writeback,
            state=provisional_writeback.status,
        )
        final_report = _with_writeback(report, terminal_journal, provisional_writeback)
        persist_writeback_outcome(final_report, artifact_paths)
        finalize_run_artifacts(final_report, artifact_paths)
        return final_report

    input_drift = _public_input_drift(_input_drift(input_snapshots), request)
    if input_drift:
        error = "input changed after pipeline entry: " + ", ".join(sorted(input_drift))
        writeback = WritebackResult(
            status="rejected",
            before_hashes=before_hashes,
            after_hashes=_current_prompt_hashes(prompt_snapshot),
            error=error,
        )
        final_journal = _terminal_journal(
            writeback_journal,
            writeback,
            state="conflict",
            input_drift=input_drift,
        )
        final_report = _with_writeback(report, final_journal, writeback, input_drift=input_drift)
        persist_writeback_outcome(final_report, artifact_paths)
        finalize_run_artifacts(final_report, artifact_paths)
        return final_report

    committing_journal = dict(writeback_journal)
    committing_journal.update({"state": "committing", "report_phase": "writeback"})
    persist_writeback_journal(artifact_paths, committing_journal)
    try:
        writeback = commit_prompt_bundle(
            prompt_snapshot,
            dict(candidate_bundles[selected_candidate]),
        )
    except ConcurrentPromptUpdateError as error:
        writeback = WritebackResult(
            status="rejected",
            before_hashes=before_hashes,
            after_hashes=_current_prompt_hashes(prompt_snapshot),
            error=_sanitize_error_message(
                f"prompt writeback conflict: {error}",
                request,
            ),
        )
        terminal_state = "conflict"
    else:
        writeback = _sanitize_writeback_result(writeback, request)
        terminal_state = writeback.status
    final_journal = _terminal_journal(
        committing_journal,
        writeback,
        state=terminal_state,
    )
    final_report = _with_writeback(report, final_journal, writeback)
    try:
        persist_writeback_journal(artifact_paths, final_journal)
    except Exception as persist_error:
        unknown_journal = dict(final_journal)
        unknown_journal.update(
            {
                "state": "unknown",
                "error": _sanitize_error_message(
                    f"failed to persist terminal writeback outcome: {persist_error}",
                    request,
                ),
                "observed_hashes": _current_prompt_hashes(prompt_snapshot),
            }
        )
        try:
            persist_writeback_journal(artifact_paths, unknown_journal)
        except Exception:
            pass
        raise
    persist_writeback_outcome(final_report, artifact_paths)
    finalize_run_artifacts(final_report, artifact_paths)
    return final_report


def _snapshot_pipeline_inputs(request: PipelineRequest, *, run_id: str) -> _InputSnapshots:
    sources: dict[str, Path] = {
        "train": Path(request.train_path),
        "validation": Path(request.validation_path),
        "optimizer": Path(request.optimizer_config_path),
    }
    if request.gate_config_path is not None:
        sources["gate_config"] = Path(request.gate_config_path)
    snapshots: dict[str, _InputFileSnapshot] = {}
    try:
        for role, source in sources.items():
            content = source.read_bytes()
            fd, temp_name = tempfile.mkstemp(
                dir=source.parent,
                prefix=f".{run_id}.{role}.",
                suffix=f".snapshot-{source.name}",
            )
            snapshot_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    fd = -1
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException as primary_error:
                try:
                    snapshot_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    add_note = getattr(primary_error, "add_note", None)
                    if add_note is not None:
                        add_note(f"failed to remove partial input snapshot: {cleanup_error}")
                raise
            finally:
                if fd >= 0:
                    os.close(fd)
            snapshots[role] = _InputFileSnapshot(
                role=role,
                original_path=source,
                snapshot_path=snapshot_path,
                sha256=hashlib.sha256(content).hexdigest(),
            )
    except BaseException as primary_error:
        try:
            _InputSnapshots(snapshots).cleanup()
        except OSError as cleanup_error:
            add_note = getattr(primary_error, "add_note", None)
            if add_note is not None:
                add_note(str(cleanup_error))
        raise
    return _InputSnapshots(snapshots)


def _claim_run_directory(output_dir: str | Path, *, run_id: str) -> Path:
    runs_root = Path(output_dir) / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_root = runs_root / run_id
    try:
        run_root.mkdir()
    except FileExistsError as error:
        raise ValueError(f"run_id {run_id!r} already exists in {runs_root}") from error
    return run_root


def _verify_snapshot_integrity(
    snapshots: _InputSnapshots,
    *requested_roles: str,
) -> None:
    roles = requested_roles or tuple(snapshots.files)
    for role in roles:
        item = snapshots.files[role]
        try:
            observed = hashlib.sha256(item.snapshot_path.read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError(f"immutable input snapshot {role!r} is unavailable: {error}") from error
        if observed != item.sha256:
            raise ValueError(f"immutable input snapshot changed for {role}")


def _effective_gate_config(
    request: PipelineRequest,
    snapshots: _InputSnapshots,
    *,
    raw_config: dict[str, Any],
) -> dict[str, Any]:
    if "gate_config" in snapshots.files:
        payload = read_json(snapshots.path("gate_config"))
        gate_payload = payload.get("gate", payload)
        if gate_payload is None:
            gate_payload = {}
        if not isinstance(gate_payload, dict):
            raise ValueError("gate config field 'gate' must be an object")
    elif request.gate_config_source == "optimizer":
        gate_payload = parse_optimizer_config(
            raw_config,
            path=request.optimizer_config_path,
        ).gate.to_dict()
    elif request.gate_config_source == "request":
        gate_payload = request.gate_config
    else:
        raise ValueError(f"unknown gate_config_source: {request.gate_config_source!r}")
    if not isinstance(gate_payload, dict):
        raise ValueError("gate config must be an object")
    return _parse_gate_config(gate_payload, path="gate config snapshot").to_dict()


def _validate_backend_seed(backend: Any, effective_seed: int) -> None:
    if not hasattr(backend, "seed"):
        return
    backend_seed = getattr(backend, "seed")
    if (
        isinstance(backend_seed, bool)
        or not isinstance(backend_seed, int)
        or backend_seed != effective_seed
    ):
        raise ValueError(
            f"fake backend seed {backend_seed!r} does not match effective seed {effective_seed}"
        )


def _strict_case_metadata(path: str | Path, *, role: str) -> dict[str, bool]:
    payload = read_json(path)
    has_standard = "eval_cases" in payload
    has_legacy = "cases" in payload
    if has_standard == has_legacy:
        raise ValueError(
            f"{role} evalset must contain exactly one of eval_cases or cases"
        )
    if has_standard:
        cases = payload["eval_cases"]
        standard = True
    else:
        cases = payload["cases"]
        standard = False
    if not isinstance(cases, list):
        raise ValueError(f"{role} evalset cases must be a list")
    if not cases:
        raise ValueError(f"{role} evalset cases must not be empty")

    metadata: dict[str, bool] = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"{role} evalset case {index} must be an object")
        if standard:
            case_id = case.get("eval_id")
            protected = False
            if "session_input" in case:
                session_input = case["session_input"]
                if not isinstance(session_input, dict):
                    raise ValueError(f"{role} evalset case {index} session_input must be an object")
                state = session_input.get("state", {})
                if state is None:
                    state = {}
                if not isinstance(state, dict):
                    raise ValueError(f"{role} evalset case {index} session_input.state must be an object")
                protected = state.get("eval_optimize_protected", False)
        else:
            case_id = case["case_id"] if "case_id" in case else case.get("id")
            protected = case.get("protected", False)

        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{role} evalset case {index} case ID must be a non-empty string")
        if not isinstance(protected, bool):
            raise ValueError(f"{role} evalset case {case_id!r} protected metadata must be a boolean")
        if case_id in metadata:
            raise ValueError(f"{role} evalset contains duplicate case ID {case_id!r}")
        metadata[case_id] = protected
    return metadata


def _input_drift(snapshots: _InputSnapshots) -> dict[str, dict[str, Any]]:
    drift: dict[str, dict[str, Any]] = {}
    for role, item in snapshots.files.items():
        try:
            after_hash = hashlib.sha256(item.original_path.read_bytes()).hexdigest()
        except OSError as error:
            drift[role] = {
                "before_hash": item.sha256,
                "after_hash": None,
                "error": str(error),
            }
            continue
        if after_hash != item.sha256:
            drift[role] = {
                "before_hash": item.sha256,
                "after_hash": after_hash,
                "error": None,
            }
    return drift


def _public_input_drift(
    drift: dict[str, dict[str, Any]],
    request: PipelineRequest,
) -> dict[str, dict[str, Any]]:
    public: dict[str, dict[str, Any]] = {}
    for role, details in drift.items():
        sanitized = dict(details)
        if sanitized.get("error"):
            sanitized["error"] = _sanitize_error_message(str(sanitized["error"]), request)
        public[role] = sanitized
    return public


def _prompt_bundle_hashes(bundle: Mapping[str, str]) -> dict[str, str]:
    return {name: hashlib.sha256(prompt.encode("utf-8")).hexdigest() for name, prompt in bundle.items()}


def _current_prompt_hashes(snapshot: Any) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, prompt_file in snapshot.files.items():
        try:
            hashes[name] = hashlib.sha256(prompt_file.path.read_bytes()).hexdigest()
        except OSError:
            continue
    return hashes


def _terminal_journal(
    journal: dict[str, Any],
    writeback: WritebackResult,
    *,
    state: str,
    input_drift: dict[str, Any] | None = None,
) -> dict[str, Any]:
    terminal = dict(journal)
    terminal.update(
        {
            "state": state,
            "report_phase": "final",
            "after_hashes": dict(writeback.after_hashes),
            "error": writeback.error,
        }
    )
    if input_drift:
        terminal["input_drift"] = input_drift
    return terminal


def _with_writeback(
    report: OptimizationReport,
    journal: dict[str, Any],
    writeback: WritebackResult,
    *,
    input_drift: dict[str, Any] | None = None,
) -> OptimizationReport:
    audit = dict(report.audit)
    audit["writeback_journal"] = journal
    if input_drift:
        audit["input_drift"] = input_drift
    return replace(report, audit=audit, writeback=writeback)


def _sanitize_writeback_result(
    writeback: WritebackResult,
    request: PipelineRequest,
) -> WritebackResult:
    if writeback.error is None:
        return writeback
    return replace(
        writeback,
        error=_sanitize_error_message(writeback.error, request),
    )


def _sanitize_error_message(message: str, request: PipelineRequest) -> str:
    replacements: list[tuple[str, str]] = []
    raw_paths: list[str | Path] = [
        request.train_path,
        request.validation_path,
        request.optimizer_config_path,
        request.output_dir,
        *request.target_prompt_paths.values(),
    ]
    if request.gate_config_path is not None:
        raw_paths.append(request.gate_config_path)
    for raw_path in raw_paths:
        path = Path(raw_path)
        display = _display_path(path)
        replacements.append((str(path), display))
        replacements.append((str(path.resolve()), display))
    sanitized = message
    for source, replacement in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        sanitized = sanitized.replace(source, replacement)
    return sanitized


def _decode_snapshot(snapshot: Any) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for name, prompt_file in snapshot.files.items():
        try:
            prompts[name] = prompt_file.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"target prompt {name!r} at {prompt_file.path} is not valid UTF-8") from exc
    return prompts


def _recoverable_candidate_evaluation_failure(
    error: Exception,
    *,
    stage: str,
    completed_results: list[EvalResult],
    input_snapshots: _InputSnapshots,
    prompt_snapshot: Any,
) -> dict[str, Any]:
    """Return a public failure record only after proving prompt state is safe.

    Candidate backend errors are recoverable, but source-prompt restoration,
    CAS, immutable-input, and cancellation failures remain run-fatal.
    """

    _verify_snapshot_integrity(input_snapshots)
    if _exception_chain_contains(
        error,
        (ConcurrentPromptUpdateError, PromptRestorationError),
    ):
        raise error

    expected_prompt_hashes = prompt_snapshot.hashes()
    observed_prompt_hashes = _current_prompt_hashes(prompt_snapshot)
    if observed_prompt_hashes != expected_prompt_hashes:
        changed = sorted(
            name
            for name in set(expected_prompt_hashes) | set(observed_prompt_hashes)
            if expected_prompt_hashes.get(name) != observed_prompt_hashes.get(name)
        )
        raise ConcurrentPromptUpdateError(
            "source prompt integrity changed during failed candidate evaluation: "
            + ", ".join(changed)
        ) from error

    raw_message = str(error)
    return {
        "stage": stage,
        "type": type(error).__name__,
        "message": "candidate evaluation failed; backend details withheld",
        "message_sha256": hashlib.sha256(
            raw_message.encode("utf-8", errors="replace")
        ).hexdigest(),
        "completed_splits": [result.split for result in completed_results],
        "known_evaluator_cost": round(sum(result.cost for result in completed_results), 6),
        "cost_complete": False,
    }


def _exception_chain_contains(
    error: BaseException,
    exception_types: tuple[type[BaseException], ...],
) -> bool:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, exception_types):
            return True
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def _evaluation_error_gate_decision(
    *,
    candidate_id: str,
    failure: dict[str, Any],
    cumulative_cost: float,
) -> GateDecision:
    candidate_cost = round(float(failure["known_evaluator_cost"]), 6)
    total_run_cost = round(cumulative_cost + candidate_cost, 6)
    stage = str(failure["stage"])
    reason = (
        f"reject: evaluation_error during {stage}: "
        f"{failure['type']}: {failure['message']}"
    )
    return GateDecision(
        candidate_id=candidate_id,
        accepted=False,
        reasons=[reason],
        train_score_delta=None,
        validation_score_delta=None,
        new_hard_failures=[],
        protected_regressions=[],
        validation_new_failures=[],
        excessive_score_drops=[],
        overfit_detected=None,
        candidate_cost=candidate_cost,
        cumulative_cost=round(cumulative_cost, 6),
        total_run_cost=total_run_cost,
        cost=candidate_cost,
        gate_status="not_applied",
        gate_not_applied_reason="evaluation_error",
        not_applied_checks=[
            "score_delta",
            "validation_improvement",
            "new_failures",
            "hard_failures",
            "protected_regressions",
            "per_case_score_drop",
            "overfit",
        ],
    )


def _validated_candidate_bundles(
    candidates: list[CandidatePrompt],
    *,
    expected_fields: set[str],
) -> dict[str, Mapping[str, str]]:
    bundles: dict[str, Mapping[str, str]] = {}
    casefold_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate.candidate_id
        _validate_candidate_id(candidate_id)
        for field_name, value in (
            ("prompt", candidate.prompt),
            ("rationale", candidate.rationale),
            ("prompt_diff", candidate.prompt_diff),
        ):
            _validate_utf8_text(
                value,
                context=f"candidate {candidate_id!r} {field_name}",
            )
        if candidate_id.casefold() == "baseline":
            raise ValueError("candidate_id 'baseline' is reserved")
        if candidate_id in bundles:
            raise ValueError(f"duplicate candidate_id: {candidate_id}")
        if candidate_id.casefold() in casefold_ids:
            raise ValueError(f"candidate_id values must be case-insensitively unique: {candidate_id}")
        casefold_ids.add(candidate_id.casefold())
        bundle = candidate.bundle()
        actual_fields = set(bundle)
        if actual_fields != expected_fields:
            raise ValueError(
                f"candidate {candidate_id!r} bundle fields must exactly match target prompt fields; "
                f"missing={sorted(expected_fields - actual_fields)}, "
                f"extra={sorted(actual_fields - expected_fields)}"
            )
        for field_name, prompt_text in bundle.items():
            if not isinstance(prompt_text, str) or not prompt_text:
                raise ValueError(
                    f"candidate {candidate_id!r} bundle field {field_name!r} " "must be a non-empty string"
                )
            _validate_utf8_text(
                prompt_text,
                context=f"candidate {candidate_id!r} bundle field {field_name!r}",
            )
        bundles[candidate_id] = MappingProxyType(dict(bundle))
    return bundles


def _validate_candidate_id(candidate_id: Any) -> None:
    try:
        validate_artifact_component(candidate_id, context="candidate_id")
    except ValueError as error:
        raise ValueError(f"candidate_id is not artifact-safe: {candidate_id!r}") from error


def _validate_utf8_text(value: Any, *, context: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{context} must be valid UTF-8") from error


def _result_pair_is_comparable(baseline: EvalResult, candidate: EvalResult) -> bool:
    baseline_ids = [case.case_id for case in baseline.cases]
    candidate_ids = [case.case_id for case in candidate.cases]
    return (
        bool(baseline_ids)
        and bool(candidate_ids)
        and (
            len(baseline_ids) == len(set(baseline_ids))
            and len(candidate_ids) == len(set(candidate_ids))
            and set(baseline_ids) == set(candidate_ids)
        )
    )


def _validate_eval_result(
    result: EvalResult,
    *,
    expected_prompt_id: str,
    expected_split: str,
    expected_case_ids: set[str] | None = None,
) -> None:
    if result.prompt_id != expected_prompt_id:
        raise ValueError(
            f"evaluation result prompt_id mismatch: expected {expected_prompt_id!r}, " f"got {result.prompt_id!r}"
        )
    if result.split != expected_split:
        raise ValueError(
            f"EvalResult split mismatch for {expected_prompt_id!r}: "
            f"expected {expected_split!r}, got {result.split!r}"
        )
    score = _finite_number(
        result.score,
        context=f"EvalResult score for {expected_prompt_id!r}",
        minimum=0.0,
        maximum=1.0,
    )
    _finite_number(
        result.cost,
        context=f"EvalResult cost for {expected_prompt_id!r}",
        minimum=0.0,
    )
    if not isinstance(result.passed, bool):
        raise ValueError(f"EvalResult passed for {expected_prompt_id!r} must be a boolean")
    case_ids: set[str] = set()
    for case in result.cases:
        if not isinstance(case.case_id, str) or not case.case_id:
            raise ValueError("CaseResult case_id must be a non-empty string")
        if case.case_id in case_ids:
            # Duplicate IDs are handled by the fail-closed gate, but the numeric
            # protocol boundary still validates each record independently.
            pass
        case_ids.add(case.case_id)
        if case.split != expected_split:
            raise ValueError(
                f"CaseResult {case.case_id!r} split mismatch for {expected_prompt_id!r}: "
                f"expected {expected_split!r}, got {case.split!r}"
            )
        _finite_number(
            case.score,
            context=f"CaseResult {case.case_id!r} score",
            minimum=0.0,
            maximum=1.0,
        )
        _finite_number(
            case.cost,
            context=f"CaseResult {case.case_id!r} cost",
            minimum=0.0,
        )
        if not isinstance(case.passed, bool) or not isinstance(case.hard_failed, bool):
            raise ValueError(f"CaseResult {case.case_id!r} passed/hard_failed must be booleans")
        if not isinstance(case.metrics, dict):
            raise ValueError(f"CaseResult {case.case_id!r} metrics must be an object")
        for metric_name, metric_value in case.metrics.items():
            if not isinstance(metric_name, str) or not metric_name:
                raise ValueError(f"CaseResult {case.case_id!r} metric names must be strings")
            _finite_number(
                metric_value,
                context=f"CaseResult {case.case_id!r} metric {metric_name!r}",
            )
    if result.cases:
        mean_score = sum(float(case.score) for case in result.cases) / len(result.cases)
        if not math.isclose(score, mean_score, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"EvalResult score for {expected_prompt_id!r} must equal the case mean")
    expected_passed = bool(result.cases) and all(case.passed for case in result.cases)
    if result.passed != expected_passed:
        raise ValueError(f"EvalResult passed for {expected_prompt_id!r} must agree with case results")
    if expected_case_ids is not None:
        observed_ids = [case.case_id for case in result.cases]
        if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != expected_case_ids:
            raise ValueError(
                f"{expected_prompt_id} {expected_split} result must exactly match dataset case IDs; "
                f"expected={sorted(expected_case_ids)}, observed={sorted(set(observed_ids))}"
            )


def _validate_optimization_result(result: OptimizationResult) -> None:
    if not isinstance(result.candidates, list) or not isinstance(result.rounds, list):
        raise ValueError("optimization result candidates and rounds must be lists")
    _validate_cost_summary(result.cost, context="optimization cost")
    for index, round_record in enumerate(result.rounds):
        if (
            isinstance(round_record.round_id, bool)
            or not isinstance(round_record.round_id, int)
            or round_record.round_id <= 0
        ):
            raise ValueError(f"optimization round {index} round_id must be a positive integer")
        _validate_candidate_id(round_record.candidate_id)
        _finite_number(
            round_record.duration_seconds,
            context=f"optimization round {round_record.round_id} duration",
            minimum=0.0,
        )
        if not isinstance(round_record.metrics, dict):
            raise ValueError(f"optimization round {round_record.round_id} metrics must be an object")
        for metric_name, metric_value in round_record.metrics.items():
            _finite_number(
                metric_value,
                context=f"optimization round {round_record.round_id} metric {metric_name!r}",
            )
        _validate_cost_summary(
            round_record.cost,
            context=f"optimization round {round_record.round_id} cost",
        )
    if not isinstance(result.raw_summary, dict):
        raise ValueError("optimization raw_summary must be an object")
    _validate_nested_numbers(result.raw_summary, context="optimization raw_summary")


def _validate_cost_summary(summary: CostSummary, *, context: str) -> None:
    components = {
        "optimizer": _finite_number(summary.optimizer, context=f"{context} optimizer", minimum=0.0),
        "evaluator": _finite_number(summary.evaluator, context=f"{context} evaluator", minimum=0.0),
        "agent": _finite_number(summary.agent, context=f"{context} agent", minimum=0.0),
    }
    total = _finite_number(summary.total, context=f"{context} total", minimum=0.0)
    if not isinstance(summary.complete, bool):
        raise ValueError(f"{context} complete must be a boolean")
    if summary.reported_optimizer_cost is not None:
        _finite_number(
            summary.reported_optimizer_cost,
            context=f"{context} reported_optimizer_cost",
            minimum=0.0,
        )
    if summary.complete and not math.isclose(
        total,
        sum(components.values()),
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise ValueError(f"{context} total must equal components when complete")


def _reported_optimizer_cost(summary: CostSummary) -> float | None:
    if summary.complete:
        return None
    if summary.reported_optimizer_cost is not None:
        return float(summary.reported_optimizer_cost)
    return float(summary.total)


def _validate_nested_numbers(value: Any, *, context: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        _finite_number(value, context=context)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{context} keys must be JSON-compatible strings")
            _validate_nested_numbers(item, context=f"{context}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_nested_numbers(item, context=f"{context}[{index}]")
        return
    raise ValueError(f"{context} contains non JSON-compatible value {type(value).__name__}")


def _finite_number(
    value: Any,
    *,
    context: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{context} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{context} must be a finite number")
    if minimum is not None and number < minimum:
        raise ValueError(f"{context} must be non-negative" if minimum == 0 else f"{context} is too small")
    if maximum is not None and number > maximum:
        raise ValueError(f"{context} must be between {minimum:g} and {maximum:g}")
    return number


def _gate_config_with_dataset_protection(
    gate_config: dict[str, Any],
    *,
    validation_metadata: dict[str, bool],
) -> dict[str, Any]:
    effective = dict(gate_config)
    configured_ids = effective.get("protected_case_ids", [])
    if not isinstance(configured_ids, list) or not all(
        isinstance(case_id, str) and bool(case_id.strip()) for case_id in configured_ids
    ):
        raise ValueError("gate protected_case_ids must be a list of non-empty strings")
    missing_ids = sorted(set(configured_ids) - set(validation_metadata))
    if missing_ids:
        raise ValueError("gate protected_case_ids references missing validation cases: " + ", ".join(missing_ids))
    protected_ids = set(configured_ids)
    protected_ids.update(case_id for case_id, protected in validation_metadata.items() if protected)
    effective["protected_case_ids"] = sorted(protected_ids)
    return effective


def _select_candidate(
    candidate_records: list[dict[str, Any]],
    gate_decisions: list[Any],
) -> str | None:
    decisions_by_id = {decision.candidate_id: decision for decision in gate_decisions}
    accepted: list[tuple[int, dict[str, Any]]] = []
    for index, record in enumerate(candidate_records):
        candidate = record["candidate"]
        decision = decisions_by_id[candidate.candidate_id]
        if (
            decision.accepted
            and "train_result" in record
            and "validation_result" in record
        ):
            accepted.append((index, record))
    if not accepted:
        return None
    _, selected = max(
        accepted,
        key=lambda item: (
            item[1]["validation_result"].score,
            item[1]["train_result"].score,
            -item[0],
        ),
    )
    return selected["candidate"].candidate_id


def _build_run(
    request: PipelineRequest,
    *,
    baseline_train: EvalResult,
    baseline_validation: EvalResult,
) -> dict[str, Any]:
    target_paths = {name: _display_path(path) for name, path in request.target_prompt_paths.items()}
    return {
        "run_id": request.run_id,
        "mode": request.mode,
        "trace": request.trace,
        "trace_enabled": request.trace,
        "update_source": request.update_source,
        "train_cases": len(baseline_train.cases),
        "validation_cases": len(baseline_validation.cases),
        "target_prompt_paths": target_paths,
        "prompt_source": target_paths.get("system_prompt"),
        "paths": {
            "train": _display_path(request.train_path),
            "validation": _display_path(request.validation_path),
            "optimizer": _display_path(request.optimizer_config_path),
            "prompts": target_paths,
        },
        "reproducibility_shell": "powershell",
        "reproducibility_command": _reproducibility_command(request),
    }


def _build_audit(
    *,
    request: PipelineRequest,
    started: float,
    snapshot_hashes: dict[str, str],
    input_hashes: dict[str, Any],
    config_snapshot: dict[str, Any],
    raw_config_hash: str,
    effective_seed: int,
    baseline_train: EvalResult,
    baseline_validation: EvalResult,
    candidate_records: list[dict[str, Any]],
    candidate_bundles: dict[str, Mapping[str, str]],
    optimization_raw_summary: dict[str, Any],
    optimization_cost: CostSummary,
    cost_summary: CostSummary,
    all_results_comparable: bool,
    effective_gate_config: dict[str, Any],
    writeback_journal: dict[str, Any],
) -> dict[str, Any]:
    candidate_costs = {
        record["candidate"].candidate_id: round(
            sum(
                record[result_name].cost
                for result_name in ("train_result", "validation_result")
                if result_name in record
            ),
            6,
        )
        for record in candidate_records
    }
    candidate_evaluation_failures = {
        record["candidate"].candidate_id: dict(record["evaluation_error"])
        for record in candidate_records
        if "evaluation_error" in record
    }
    candidate_prompt_hashes = {
        candidate_id: {name: hashlib.sha256(prompt.encode("utf-8")).hexdigest() for name, prompt in bundle.items()}
        for candidate_id, bundle in candidate_bundles.items()
    }
    cost_audit: dict[str, Any] = {
        "baseline": round(baseline_train.cost + baseline_validation.cost, 6),
        "candidates": candidate_costs,
        "optimization": to_jsonable(optimization_cost),
        "evaluator": cost_summary.evaluator,
        "total": cost_summary.total if cost_summary.complete else None,
        "complete": cost_summary.complete,
        "reported_optimizer_cost": cost_summary.reported_optimizer_cost,
    }
    if not cost_summary.complete:
        cost_audit["known_run_cost"] = cost_summary.total
    duration = max(time.perf_counter() - started, 1e-9)
    target_paths = {name: _display_path(path) for name, path in request.target_prompt_paths.items()}
    return {
        "seed": effective_seed,
        "duration_seconds": duration,
        "config_hash": raw_config_hash,
        "config_file_sha256": input_hashes["optimizer"],
        "redacted_config_hash": stable_config_hash(config_snapshot),
        "redacted_config_snapshot_sha256": _pretty_json_sha256(config_snapshot),
        "config_snapshot": config_snapshot,
        "gate_config_hash": stable_config_hash(effective_gate_config),
        "gate_config_snapshot": effective_gate_config,
        "writeback_journal": writeback_journal,
        "input_hashes": input_hashes,
        "input_paths": {
            "train": _display_path(request.train_path),
            "validation": _display_path(request.validation_path),
            "optimizer": _display_path(request.optimizer_config_path),
            "prompts": target_paths,
            **({"prompt": target_paths["system_prompt"]} if "system_prompt" in target_paths else {}),
        },
        "prompt_hash": snapshot_hashes.get("system_prompt"),
        "prompt_hashes": snapshot_hashes,
        "candidate_prompt_hashes": candidate_prompt_hashes,
        "candidate_artifacts": {
            record["candidate"].candidate_id: _candidate_artifact_component(
                index,
                record["candidate"].candidate_id,
            )
            for index, record in enumerate(candidate_records, start=1)
        },
        "candidate_prompts": {
            candidate_id: dict(bundle) for candidate_id, bundle in candidate_bundles.items()
        },
        "candidate_evaluation_failures": candidate_evaluation_failures,
        "prompt_diffs": {
            record["candidate"].candidate_id: record["candidate"].prompt_diff for record in candidate_records
        },
        "total_run_cost": cost_summary.total if cost_summary.complete else None,
        "known_run_cost": cost_summary.total if not cost_summary.complete else None,
        "total_run_cost_complete": cost_summary.complete,
        "cost": cost_audit,
        "sdk_result_summary": _sanitize_public_value(to_jsonable(optimization_raw_summary)),
        "sdk_result_availability": {
            "aggregate_validation_result": bool(request.mode == "sdk" and optimization_raw_summary),
            "full_train_eval_result": not any(
                failure.get("stage") == "train"
                for failure in candidate_evaluation_failures.values()
            ),
            "full_per_case_validation_delta": all_results_comparable,
        },
        "reproducibility_shell": "powershell",
        "reproducibility_command": _reproducibility_command(request),
    }


def _artifact_dir(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_artifact_component(value: str, *, context: str) -> str:
    return validate_artifact_component(value, context=context)


def _candidate_artifact_component(index: int, candidate_id: str) -> str:
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:12]
    return f"{index:03d}-{digest}"


def _display_path(raw_path: str | Path) -> str:
    path = Path(raw_path).resolve()
    try:
        relative = path.relative_to(_REPOSITORY_ROOT)
    except ValueError:
        return f"$EXTERNAL/{path.name}"
    return relative.as_posix()


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.casefold()
            if _is_secret_key(lowered):
                redacted[key_text] = "<redacted>"
            else:
                redacted[key_text] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item) for item in value]
    return value


def _pretty_json_sha256(value: Any) -> str:
    content = json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_secret_key(lowered: str) -> bool:
    normalized = lowered.replace("-", "_").replace(" ", "_")
    if normalized in {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "auth_token",
        "password",
        "passwd",
        "credentials",
        "credential",
        "secret",
        "authorization",
        "private_key",
        "signing_key",
        "ssh_key",
    }:
        return True
    if normalized.endswith("_token"):
        return True
    return any(
        marker in normalized
        for marker in (
            "api_key",
            "password",
            "passwd",
            "credential",
            "secret",
            "authorization",
            "private_key",
        )
    )


def _sanitize_public_value(value: Any) -> Any:
    value = _redact_secrets(value)
    if isinstance(value, dict):
        return {str(key): _sanitize_public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, str):
        try:
            candidate = Path(value)
            if candidate.is_absolute():
                return _display_path(candidate)
        except (OSError, ValueError):
            pass
    return value


def _reproducibility_command(request: PipelineRequest) -> str:
    command = [
        "python",
        "examples/optimization/eval_optimize_loop/run_pipeline.py",
        "--mode",
        request.mode,
        "--train",
        _display_path(request.train_path),
        "--val",
        _display_path(request.validation_path),
        "--optimizer-config",
        _display_path(request.optimizer_config_path),
        "--output-dir",
        "$OUTPUT_DIR",
        "--run-id",
        request.run_id,
    ]
    target_paths = list(request.target_prompt_paths.items())
    if set(request.target_prompt_paths) == {"system_prompt"}:
        command.extend(["--prompt", _display_path(request.target_prompt_paths["system_prompt"])])
    else:
        for name, path in target_paths:
            command.extend(["--target-prompt", f"{name}={_display_path(path)}"])
    if request.trace:
        command.append("--trace")
    if request.mode == "sdk" and request.sdk_call_agent:
        command.extend(["--sdk-call-agent", request.sdk_call_agent])
    if request.gate_config_path is not None:
        command.extend(["--gate-config", _display_path(request.gate_config_path)])
    if request.update_source:
        command.append("--update-source")
    return " ".join(_powershell_arg(item) for item in command)


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_arg(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:-]+", value):
        return value
    return _powershell_quote(value)


def _initial_writeback_journal(
    *,
    run_id: str,
    selected_candidate: str | None,
    update_source: bool,
    before_hashes: dict[str, str],
    candidate_hashes: dict[str, str],
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    if selected_candidate is None:
        state = "rejected"
    elif not update_source:
        state = "not_requested"
    else:
        state = "pending"
    return {
        "run_id": run_id,
        "state": state,
        "report_phase": "pre_write",
        "requested": update_source,
        "selected_candidate": selected_candidate,
        "before_hashes": dict(before_hashes),
        "candidate_hashes": dict(candidate_hashes),
        "input_hashes": dict(input_hashes),
        "after_hashes": {},
        "error": None,
    }
