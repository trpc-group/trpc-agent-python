"""Backend-neutral asynchronous evaluation and optimization orchestration."""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any

from .attribution import summarize_failures
from .backends import EvaluationBackend
from .backends import OptimizationBackend
from .gate import AcceptanceGate
from .loader import read_json
from .loader import sha256_file
from .loader import stable_config_hash
from .report import build_report
from .report import compute_case_deltas
from .report import finalize_run_artifacts
from .report import prepare_run_artifacts
from .schemas import CandidatePrompt
from .schemas import CostSummary
from .schemas import EvalResult
from .schemas import OptimizationReport
from .schemas import WritebackResult
from .schemas import to_jsonable
from .writeback import commit_prompt_bundle
from .writeback import snapshot_prompt_files


_ARTIFACT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


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

    snapshot = snapshot_prompt_files(request.target_prompt_paths)
    baseline_prompts = _decode_snapshot(snapshot)
    config_snapshot = read_json(request.optimizer_config_path)
    effective_gate_config = _gate_config_with_dataset_protection(
        request.gate_config,
        validation_path=request.validation_path,
    )
    run_root = Path(request.output_dir) / "runs" / run_id

    baseline_train = await evaluator.evaluate(
        prompt_id="baseline",
        prompts=baseline_prompts,
        dataset_path=request.train_path,
        split="train",
        trace=request.trace,
        artifact_dir=_artifact_dir(run_root, "evaluations", "000_baseline", "train"),
    )
    _validate_result_identity(
        baseline_train,
        expected_prompt_id="baseline",
        expected_split="train",
    )
    baseline_validation = await evaluator.evaluate(
        prompt_id="baseline",
        prompts=baseline_prompts,
        dataset_path=request.validation_path,
        split="validation",
        trace=request.trace,
        artifact_dir=_artifact_dir(run_root, "evaluations", "000_baseline", "validation"),
    )
    _validate_result_identity(
        baseline_validation,
        expected_prompt_id="baseline",
        expected_split="validation",
    )

    failure_summary = summarize_failures([baseline_train])
    optimizer_artifact_dir = _artifact_dir(run_root, "optimizer")
    optimization = await optimizer.optimize_candidates(
        baseline_prompts=baseline_prompts,
        baseline_train=baseline_train,
        failure_summary=failure_summary,
        train_path=request.train_path,
        validation_path=request.validation_path,
        config_path=request.optimizer_config_path,
        artifact_dir=optimizer_artifact_dir,
    )
    candidate_bundles = _validated_candidate_bundles(
        optimization.candidates,
        expected_fields=set(snapshot.files),
    )

    gate = AcceptanceGate(effective_gate_config)
    baseline_evaluator_cost = round(baseline_train.cost + baseline_validation.cost, 6)
    explicit_evaluator_cost = baseline_evaluator_cost
    cumulative_cost = round(baseline_evaluator_cost + optimization.cost.total, 6)
    candidate_records: list[dict[str, Any]] = []
    all_deltas = []
    gate_decisions = []
    all_results_comparable = True

    for index, candidate in enumerate(optimization.candidates, start=1):
        bundle = candidate_bundles[candidate.candidate_id]
        path_label = f"{index:03d}_{_path_label(candidate.candidate_id)}"
        train_result = await evaluator.evaluate(
            prompt_id=candidate.candidate_id,
            prompts=bundle,
            dataset_path=request.train_path,
            split="train",
            trace=request.trace,
            artifact_dir=_artifact_dir(run_root, "evaluations", path_label, "train"),
        )
        _validate_result_identity(
            train_result,
            expected_prompt_id=candidate.candidate_id,
            expected_split="train",
        )
        validation_result = await evaluator.evaluate(
            prompt_id=candidate.candidate_id,
            prompts=bundle,
            dataset_path=request.validation_path,
            split="validation",
            trace=request.trace,
            artifact_dir=_artifact_dir(run_root, "evaluations", path_label, "validation"),
        )
        _validate_result_identity(
            validation_result,
            expected_prompt_id=candidate.candidate_id,
            expected_split="validation",
        )
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
            cost_summary=optimization.cost,
            cumulative_cost=cumulative_cost,
        )
        candidate_records.append({
            "candidate": candidate,
            "train_result": train_result,
            "validation_result": validation_result,
        })
        all_deltas.extend(deltas)
        gate_decisions.append(decision)
        candidate_eval_cost = round(train_result.cost + validation_result.cost, 6)
        explicit_evaluator_cost = round(explicit_evaluator_cost + candidate_eval_cost, 6)
        cumulative_cost = decision.total_run_cost

    selected_candidate = _select_candidate(candidate_records, gate_decisions)
    cost_summary = CostSummary(
        optimizer=optimization.cost.optimizer,
        evaluator=round(optimization.cost.evaluator + explicit_evaluator_cost, 6),
        agent=optimization.cost.agent,
        total=cumulative_cost,
        complete=optimization.cost.complete,
    )
    input_hashes: dict[str, Any] = {
        "train": sha256_file(request.train_path),
        "validation": sha256_file(request.validation_path),
        "optimizer": sha256_file(request.optimizer_config_path),
        "target_prompts": snapshot.hashes(),
    }
    if request.gate_config_path is not None:
        input_hashes["gate_config"] = sha256_file(request.gate_config_path)
    before_hashes = snapshot.hashes()
    writeback_journal = _initial_writeback_journal(
        selected_candidate=selected_candidate,
        update_source=request.update_source,
        before_hashes=before_hashes,
    )
    audit = _build_audit(
        request=request,
        started=started,
        snapshot_hashes=snapshot.hashes(),
        input_hashes=input_hashes,
        config_snapshot=config_snapshot,
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
    run = _build_run(
        request,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
    )
    if selected_candidate is None:
        provisional_writeback = WritebackResult(
            status="rejected",
            before_hashes=before_hashes,
        )
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
    if selected_candidate is None:
        writeback = provisional_writeback
    elif not request.update_source:
        writeback = provisional_writeback
    else:
        writeback = commit_prompt_bundle(
            snapshot,
            candidate_bundles[selected_candidate],
        )
    final_audit = dict(report.audit)
    final_journal = dict(writeback_journal)
    final_journal.update({
        "state": writeback.status,
        "after_hashes": dict(writeback.after_hashes),
        "error": writeback.error,
    })
    final_audit["writeback_journal"] = final_journal
    final_report = replace(report, audit=final_audit, writeback=writeback)
    finalize_run_artifacts(final_report, artifact_paths)
    return final_report


def _decode_snapshot(snapshot: Any) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for name, prompt_file in snapshot.files.items():
        try:
            prompts[name] = prompt_file.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"target prompt {name!r} at {prompt_file.path} is not valid UTF-8"
            ) from exc
    return prompts


def _validated_candidate_bundles(
    candidates: list[CandidatePrompt],
    *,
    expected_fields: set[str],
) -> dict[str, dict[str, str]]:
    bundles: dict[str, dict[str, str]] = {}
    for candidate in candidates:
        candidate_id = candidate.candidate_id
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("candidate_id must be a non-empty string")
        if candidate_id == "baseline":
            raise ValueError("candidate_id 'baseline' is reserved")
        if candidate_id in bundles:
            raise ValueError(f"duplicate candidate_id: {candidate_id}")
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
                    f"candidate {candidate_id!r} bundle field {field_name!r} "
                    "must be a non-empty string"
                )
        bundles[candidate_id] = dict(bundle)
    return bundles


def _result_pair_is_comparable(baseline: EvalResult, candidate: EvalResult) -> bool:
    baseline_ids = [case.case_id for case in baseline.cases]
    candidate_ids = [case.case_id for case in candidate.cases]
    return bool(baseline_ids) and bool(candidate_ids) and (
        len(baseline_ids) == len(set(baseline_ids))
        and len(candidate_ids) == len(set(candidate_ids))
        and set(baseline_ids) == set(candidate_ids)
    )


def _validate_result_identity(
    result: EvalResult,
    *,
    expected_prompt_id: str,
    expected_split: str,
) -> None:
    if result.prompt_id != expected_prompt_id:
        raise ValueError(
            f"evaluation result prompt_id mismatch: expected {expected_prompt_id!r}, "
            f"got {result.prompt_id!r}"
        )
    if result.split != expected_split:
        raise ValueError(
            f"EvalResult split mismatch for {expected_prompt_id!r}: "
            f"expected {expected_split!r}, got {result.split!r}"
        )
    for case in result.cases:
        if case.split != expected_split:
            raise ValueError(
                f"CaseResult {case.case_id!r} split mismatch for {expected_prompt_id!r}: "
                f"expected {expected_split!r}, got {case.split!r}"
            )


def _gate_config_with_dataset_protection(
    gate_config: dict[str, Any],
    *,
    validation_path: str | Path,
) -> dict[str, Any]:
    effective = dict(gate_config)
    configured_ids = effective.get("protected_case_ids", [])
    if not isinstance(configured_ids, list) or not all(
        isinstance(case_id, str) for case_id in configured_ids
    ):
        raise ValueError("gate protected_case_ids must be a list of strings")
    protected_ids = set(configured_ids)
    payload = read_json(validation_path)
    standard_cases = payload.get("eval_cases")
    legacy_cases = payload.get("cases")
    if isinstance(standard_cases, list):
        for case in standard_cases:
            if not isinstance(case, dict):
                continue
            session_input = case.get("session_input")
            state = session_input.get("state") if isinstance(session_input, dict) else None
            protected = state.get("eval_optimize_protected", False) if isinstance(state, dict) else False
            if protected is True:
                case_id = case.get("eval_id")
                if isinstance(case_id, str) and case_id:
                    protected_ids.add(case_id)
    elif isinstance(legacy_cases, list):
        for case in legacy_cases:
            if not isinstance(case, dict) or case.get("protected") is not True:
                continue
            case_id = case.get("case_id") or case.get("id")
            if isinstance(case_id, str) and case_id:
                protected_ids.add(case_id)
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
        if decisions_by_id[candidate.candidate_id].accepted:
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
    target_paths = {name: str(path) for name, path in request.target_prompt_paths.items()}
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
            "train": str(request.train_path),
            "validation": str(request.validation_path),
            "optimizer": str(request.optimizer_config_path),
            "prompts": target_paths,
        },
        "reproducibility_command": (
            _reproducibility_command(request)
        ),
    }


def _build_audit(
    *,
    request: PipelineRequest,
    started: float,
    snapshot_hashes: dict[str, str],
    input_hashes: dict[str, Any],
    config_snapshot: dict[str, Any],
    baseline_train: EvalResult,
    baseline_validation: EvalResult,
    candidate_records: list[dict[str, Any]],
    candidate_bundles: dict[str, dict[str, str]],
    optimization_raw_summary: dict[str, Any],
    optimization_cost: CostSummary,
    cost_summary: CostSummary,
    all_results_comparable: bool,
    effective_gate_config: dict[str, Any],
    writeback_journal: dict[str, Any],
) -> dict[str, Any]:
    candidate_costs = {
        record["candidate"].candidate_id: round(
            record["train_result"].cost + record["validation_result"].cost,
            6,
        )
        for record in candidate_records
    }
    candidate_prompt_hashes = {
        candidate_id: {
            name: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            for name, prompt in bundle.items()
        }
        for candidate_id, bundle in candidate_bundles.items()
    }
    duration = max(time.perf_counter() - started, 1e-9)
    target_paths = {name: str(path) for name, path in request.target_prompt_paths.items()}
    return {
        "seed": _config_seed(config_snapshot),
        "duration_seconds": duration,
        "config_hash": stable_config_hash(config_snapshot),
        "config_snapshot": config_snapshot,
        "gate_config_hash": stable_config_hash(effective_gate_config),
        "gate_config_snapshot": effective_gate_config,
        "writeback_journal": writeback_journal,
        "input_hashes": input_hashes,
        "input_paths": {
            "train": str(request.train_path),
            "validation": str(request.validation_path),
            "optimizer": str(request.optimizer_config_path),
            "prompts": target_paths,
            **({"prompt": target_paths["system_prompt"]} if "system_prompt" in target_paths else {}),
        },
        "prompt_hash": snapshot_hashes.get("system_prompt"),
        "prompt_hashes": snapshot_hashes,
        "candidate_prompt_hashes": candidate_prompt_hashes,
        "candidate_prompts": candidate_bundles,
        "prompt_diffs": {
            record["candidate"].candidate_id: record["candidate"].prompt_diff
            for record in candidate_records
        },
        "total_run_cost": cost_summary.total,
        "cost": {
            "baseline": round(baseline_train.cost + baseline_validation.cost, 6),
            "candidates": candidate_costs,
            "optimization": to_jsonable(optimization_cost),
            "evaluator": cost_summary.evaluator,
            "total": cost_summary.total,
            "complete": cost_summary.complete,
        },
        "sdk_result_summary": to_jsonable(optimization_raw_summary),
        "sdk_result_availability": {
            "aggregate_validation_result": bool(
                request.mode == "sdk" and optimization_raw_summary
            ),
            "full_train_eval_result": True,
            "full_per_case_validation_delta": all_results_comparable,
        },
        "reproducibility_command": (
            _reproducibility_command(request)
        ),
    }


def _artifact_dir(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_artifact_component(value: str, *, context: str) -> str:
    if value in {"", ".", ".."} or not _ARTIFACT_COMPONENT_RE.fullmatch(value):
        raise ValueError(f"unsafe {context}: {value!r}")
    return value


def _path_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    if label in {"", ".", ".."}:
        return "candidate"
    return label


def _config_seed(config_snapshot: dict[str, Any]) -> Any:
    if "seed" in config_snapshot:
        return config_snapshot["seed"]
    optimize = config_snapshot.get("optimize")
    if isinstance(optimize, dict):
        algorithm = optimize.get("algorithm")
        if isinstance(algorithm, dict) and "seed" in algorithm:
            return algorithm["seed"]
    return None


def _reproducibility_command(request: PipelineRequest) -> str:
    command = [
        "python",
        "examples/optimization/eval_optimize_loop/run_pipeline.py",
        "--mode",
        request.mode,
        "--train",
        str(request.train_path),
        "--val",
        str(request.validation_path),
        "--optimizer-config",
        str(request.optimizer_config_path),
        "--output-dir",
        str(request.output_dir),
        "--run-id",
        request.run_id,
    ]
    target_paths = list(request.target_prompt_paths.items())
    if set(request.target_prompt_paths) == {"system_prompt"}:
        command.extend(["--prompt", str(request.target_prompt_paths["system_prompt"])])
    else:
        for name, path in target_paths:
            command.extend(["--target-prompt", f"{name}={path}"])
    if request.trace:
        command.append("--trace")
    if request.mode == "sdk" and request.sdk_call_agent:
        command.extend(["--sdk-call-agent", request.sdk_call_agent])
    if request.gate_config_path is not None:
        command.extend(["--gate-config", str(request.gate_config_path)])
    if request.update_source:
        command.append("--update-source")
    return shlex.join(command)


def _initial_writeback_journal(
    *,
    selected_candidate: str | None,
    update_source: bool,
    before_hashes: dict[str, str],
) -> dict[str, Any]:
    if selected_candidate is None:
        state = "rejected"
    elif not update_source:
        state = "not_requested"
    else:
        state = "pending"
    return {
        "state": state,
        "requested": update_source,
        "selected_candidate": selected_candidate,
        "before_hashes": dict(before_hashes),
        "after_hashes": {},
        "error": None,
    }
