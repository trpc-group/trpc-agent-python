"""Run the deterministic Evaluation + Optimization closed-loop example."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shlex
import sys
import tempfile
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from eval_loop.backends import FakeBackend
from eval_loop.backends import SDKBackend
from eval_loop.config import validate_inputs
from eval_loop.gate import AcceptanceGate
from eval_loop.loader import load_eval_cases
from eval_loop.loader import load_optimizer_config
from eval_loop.loader import load_prompt
from eval_loop.loader import sha256_file
from eval_loop.loader import stable_config_hash
from eval_loop.report import REPRODUCIBILITY_COMMAND
from eval_loop.report import build_report
from eval_loop.report import compute_case_deltas
from eval_loop.report import write_reports
from eval_loop.schemas import CandidatePrompt
from eval_loop.schemas import EvalResult
from eval_loop.schemas import GateDecision
from eval_loop.schemas import OptimizationReport


DEFAULT_TRAIN = HERE / "data" / "train.evalset.json"
DEFAULT_VAL = HERE / "data" / "val.evalset.json"
DEFAULT_OPTIMIZER_CONFIG = HERE / "data" / "optimizer.json"
DEFAULT_PROMPT = HERE / "prompts" / "baseline_system_prompt.txt"
DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "eval-optimize-loop"
TARGET_PROMPT_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def run_pipeline(
    *,
    train_path: str | Path = DEFAULT_TRAIN,
    val_path: str | Path = DEFAULT_VAL,
    optimizer_config_path: str | Path = DEFAULT_OPTIMIZER_CONFIG,
    prompt_path: str | Path = DEFAULT_PROMPT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    mode: str = "fake",
    fake_model: bool = True,
    fake_judge: bool = True,
    trace: bool = False,
    sdk_call_agent: str | None = None,
    update_source: bool = False,
    gate_config_path: str | Path | None = None,
    target_prompts: list[str] | None = None,
    run_id: str | None = None,
) -> OptimizationReport:
    """Run baseline eval, fake optimization, validation gate, and reports."""

    if mode not in {"fake", "sdk"}:
        raise ValueError("field 'mode' must be one of: fake, sdk")
    if run_id is not None:
        run_id = validate_run_id(run_id)
    if mode == "fake" and (not fake_model or not fake_judge):
        raise ValueError(
            "fake mode requires fake_model=True and fake_judge=True. Pass --fake-model --fake-judge "
            "or use --mode sdk with --sdk-call-agent module:function."
        )

    if mode == "sdk":
        optimizer_config_dict = _read_json_object_for_audit(optimizer_config_path)
        baseline_prompt = load_prompt(prompt_path)
        sdk_artifact_dir = Path(output_dir) / "sdk_optimizer"
        wrapper_gate_config = _load_sdk_gate_config(gate_config_path)
        target_prompt_paths = _parse_target_prompt_paths(target_prompts, default_prompt_path=prompt_path)
        sdk_backend = SDKBackend(
            prompt_path=prompt_path,
            call_agent_path=sdk_call_agent,
            update_source=update_source,
            target_prompt_paths=target_prompt_paths,
        )
        candidates = sdk_backend.optimize(
            baseline_prompt=baseline_prompt,
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=optimizer_config_path,
            output_dir=sdk_artifact_dir,
        )
        report = _build_sdk_report(
            candidates=candidates,
            sdk_backend=sdk_backend,
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=optimizer_config_path,
            prompt_path=prompt_path,
            output_dir=output_dir,
            trace=trace,
            update_source=update_source,
            train_case_count=_count_cases(train_path),
            validation_case_count=_count_cases(val_path),
            optimizer_config_dict=optimizer_config_dict,
            gate_config=wrapper_gate_config,
            gate_config_path=gate_config_path,
            target_prompt_paths=target_prompt_paths,
            sdk_call_agent=sdk_call_agent,
            run_id=run_id,
        )
        if run_id is None:
            _resolve_default_sdk_run_id_collision(report, output_dir)
        write_reports(report, output_dir)
        return report

    optimizer_config = load_optimizer_config(optimizer_config_path)
    train_cases = load_eval_cases(train_path, split="train")
    validation_cases = load_eval_cases(val_path, split="validation")
    validate_inputs(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        train_cases=train_cases,
        validation_cases=validation_cases,
        config=optimizer_config,
    )

    seed = optimizer_config.seed
    baseline_prompt = load_prompt(prompt_path)
    backend = FakeBackend(seed=seed, trace_enabled=trace)

    baseline = CandidatePrompt(
        candidate_id="baseline",
        prompt=baseline_prompt,
        rationale="Prompt source file before optimization.",
        prompt_diff="",
    )
    baseline_train = backend.evaluate(
        prompt_id=baseline.candidate_id,
        prompt=baseline.prompt,
        cases=train_cases,
        split="train",
    )
    baseline_validation = backend.evaluate(
        prompt_id=baseline.candidate_id,
        prompt=baseline.prompt,
        cases=validation_cases,
        split="validation",
    )

    candidates = backend.optimize(
        baseline_prompt=baseline_prompt,
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        output_dir=output_dir,
    )
    gate = AcceptanceGate(optimizer_config.gate.to_dict())

    candidate_records: list[dict[str, Any]] = []
    all_deltas = []
    gate_decisions = []
    cumulative_cost = round(baseline_train.cost + baseline_validation.cost, 6)
    for candidate in candidates:
        train_result = backend.evaluate(
            prompt_id=candidate.candidate_id,
            prompt=candidate.prompt,
            cases=train_cases,
            split="train",
        )
        validation_result = backend.evaluate(
            prompt_id=candidate.candidate_id,
            prompt=candidate.prompt,
            cases=validation_cases,
            split="validation",
        )
        deltas = compute_case_deltas(
            candidate_id=candidate.candidate_id,
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=train_result,
            candidate_validation=validation_result,
        )
        decision = gate.decide(
            candidate_id=candidate.candidate_id,
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=train_result,
            candidate_validation=validation_result,
            deltas=deltas,
            cumulative_cost=cumulative_cost,
        )
        candidate_records.append({
            "candidate": candidate,
            "train_result": train_result,
            "validation_result": validation_result,
        })
        all_deltas.extend(deltas)
        gate_decisions.append(decision)
        cumulative_cost = decision.total_run_cost

    selected_candidate = _select_candidate(candidate_records, gate_decisions)
    input_hashes = _input_hashes(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        prompt_path=prompt_path,
    )
    audit = _build_audit(
        seed=seed,
        config_hash=stable_config_hash(optimizer_config.to_dict()),
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        candidates=candidates,
        input_hashes=input_hashes,
        input_paths={
            "train": str(train_path),
            "validation": str(val_path),
            "optimizer": str(optimizer_config_path),
            "prompt": str(prompt_path),
        },
    )
    run = {
        "run_id": f"eval_optimize_loop_seed_{seed}",
        "mode": mode,
        "fake_model": fake_model,
        "fake_judge": fake_judge,
        "trace_enabled": trace,
        "train_cases": len(train_cases),
        "validation_cases": len(validation_cases),
        "update_source": update_source,
        "reproducibility_command": REPRODUCIBILITY_COMMAND,
        "paths": {
            "train": str(train_path),
            "validation": str(val_path),
            "optimizer": str(optimizer_config_path),
            "prompt": str(prompt_path),
        },
        "prompt_source": str(prompt_path),
    }
    report = build_report(
        run=run,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        per_case_deltas=all_deltas,
        gate_decisions=gate_decisions,
        selected_candidate=selected_candidate,
        audit=audit,
    )
    write_reports(report, output_dir)
    return report


def _select_candidate(candidate_records: list[dict[str, Any]], gate_decisions: list) -> str | None:
    decisions_by_id = {decision.candidate_id: decision for decision in gate_decisions}
    accepted = []
    for index, record in enumerate(candidate_records):
        candidate = record["candidate"]
        decision = decisions_by_id[candidate.candidate_id]
        if decision.accepted:
            accepted.append((index, record))
    if not accepted:
        return None
    index, record = max(
        accepted,
        key=lambda item: (
            item[1]["validation_result"].score,
            item[1]["train_result"].score,
            -item[0],
        ),
    )
    return record["candidate"].candidate_id


def _build_audit(
    *,
    seed: int,
    config_hash: str,
    baseline_train,
    baseline_validation,
    candidate_records: list[dict[str, Any]],
    candidates: list[CandidatePrompt],
    input_hashes: dict[str, str],
    input_paths: dict[str, str],
) -> dict[str, Any]:
    baseline_cost = round(baseline_train.cost + baseline_validation.cost, 6)
    candidate_costs = {
        record["candidate"].candidate_id: round(record["train_result"].cost + record["validation_result"].cost, 6)
        for record in candidate_records
    }
    total_cost = round(baseline_cost + sum(candidate_costs.values()), 6)
    candidate_prompt_hashes = {
        candidate.candidate_id: hashlib.sha256(candidate.prompt.encode("utf-8")).hexdigest()
        for candidate in candidates
    }
    return {
        "seed": seed,
        "duration_seconds": 0.0,
        "config_hash": config_hash,
        "input_hashes": input_hashes,
        "input_paths": input_paths,
        "prompt_hash": input_hashes["prompt"],
        "candidate_prompt_hashes": candidate_prompt_hashes,
        "total_run_cost": total_cost,
        "cost": {
            "baseline": baseline_cost,
            "candidates": candidate_costs,
            "total": total_cost,
        },
        "candidate_prompts": {
            candidate.candidate_id: {
                "rationale": candidate.rationale,
                "prompt": candidate.prompt,
                "prompt_diff": candidate.prompt_diff,
            }
            for candidate in candidates
        },
        "prompt_diffs": {
            candidate.candidate_id: candidate.prompt_diff
            for candidate in candidates
        },
        "reproducibility_command": REPRODUCIBILITY_COMMAND,
    }


def _build_sdk_report(
    *,
    candidates: list[CandidatePrompt],
    sdk_backend: SDKBackend,
    train_path: str | Path,
    val_path: str | Path,
    optimizer_config_path: str | Path,
    prompt_path: str | Path,
    output_dir: str | Path,
    trace: bool,
    update_source: bool,
    train_case_count: int | None,
    validation_case_count: int | None,
    optimizer_config_dict: dict[str, Any],
    gate_config: dict[str, float],
    gate_config_path: str | Path | None,
    target_prompt_paths: dict[str, str | Path],
    sdk_call_agent: str | None,
    run_id: str | None,
) -> OptimizationReport:
    input_hashes = _input_hashes(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        prompt_path=prompt_path,
    )
    sdk_summary = sdk_backend.last_result_summary or {}
    baseline_pass_rate = _summary_float(sdk_summary, "baseline_pass_rate", 0.0, required=True)
    best_pass_rate = _summary_float(sdk_summary, "best_pass_rate", baseline_pass_rate, required=True)
    pass_rate_improvement = _summary_float(
        sdk_summary,
        "pass_rate_improvement",
        best_pass_rate - baseline_pass_rate,
        required=True,
    )
    total_llm_cost = _summary_float(sdk_summary, "total_llm_cost", 0.0, required=True)
    duration_seconds = _summary_float(sdk_summary, "duration_seconds", 0.0)
    effective_run_id = run_id or _default_sdk_run_id(sdk_summary)
    target_prompt_hashes = {
        name: sha256_file(path)
        for name, path in target_prompt_paths.items()
    }
    input_hashes["target_prompts"] = target_prompt_hashes
    if gate_config_path:
        input_hashes["gate_config"] = sha256_file(gate_config_path)
    availability = {
        "aggregate_validation_result": True,
        "full_train_eval_result": False,
        "full_per_case_validation_delta": False,
    }
    score_explanation = (
        "SDK mode uses OptimizeResult aggregate validation metrics. "
        "The full train EvalResult compatibility field is unavailable and keeps score 0.0; "
        "full per-case validation deltas are unavailable and listed in not_applied_checks."
    )

    baseline_train = EvalResult(prompt_id="baseline", split="train", score=0.0, passed=False, cost=0.0, cases=[])
    baseline_validation = EvalResult(
        prompt_id="baseline",
        split="validation",
        score=baseline_pass_rate,
        passed=baseline_pass_rate >= 1.0,
        cost=0.0,
        cases=[],
    )
    candidate_records = [
        {
            "candidate": candidate,
            "train_result": EvalResult(
                prompt_id=candidate.candidate_id,
                split="train",
                score=0.0,
                passed=False,
                cost=0.0,
                cases=[],
            ),
            "validation_result": EvalResult(
                prompt_id=candidate.candidate_id,
                split="validation",
                score=best_pass_rate,
                passed=best_pass_rate >= baseline_pass_rate,
                cost=total_llm_cost,
                cases=[],
            ),
            "gate_status": "partial_applied",
            "gate_not_applied_reason": "SDK OptimizeResult exposes aggregate scores but not full per-case deltas",
            "sdk_result_summary": sdk_summary,
        }
        for candidate in candidates
    ]
    prompt_hashes = {
        candidate.candidate_id: hashlib.sha256(candidate.prompt.encode("utf-8")).hexdigest()
        for candidate in candidates
    }
    field_prompt_hashes = _candidate_prompt_hashes_by_field(candidates, sdk_summary)
    audit = {
        "seed": None,
        "duration_seconds": duration_seconds,
        "config_hash": stable_config_hash(optimizer_config_dict),
        "input_hashes": input_hashes,
        "input_paths": {
            "train": str(train_path),
            "validation": str(val_path),
            "optimizer": str(optimizer_config_path),
            "prompt": str(prompt_path),
        },
        "prompt_hash": input_hashes["prompt"],
        "candidate_prompt_hashes": prompt_hashes,
        "candidate_prompt_hashes_by_field": field_prompt_hashes,
        "target_prompt_hashes": target_prompt_hashes,
        "sdk_result_availability": availability,
        "sdk_score_explanation": score_explanation,
        "wrapper_gate_config": dict(gate_config),
        "wrapper_gate_config_path": str(gate_config_path) if gate_config_path else None,
        "total_run_cost": total_llm_cost,
        "cost": {
            "baseline": 0.0,
            "candidates": {candidate.candidate_id: total_llm_cost for candidate in candidates},
            "total": total_llm_cost,
        },
        "candidate_prompts": {
            candidate.candidate_id: {
                "rationale": candidate.rationale,
                "prompt": candidate.prompt,
                "prompt_diff": candidate.prompt_diff,
            }
            for candidate in candidates
        },
        "prompt_diffs": {candidate.candidate_id: candidate.prompt_diff for candidate in candidates},
        "sdk_artifact_dir": sdk_backend.last_artifact_dir or str(Path(output_dir) / "sdk_optimizer"),
        "sdk_result_summary": sdk_summary,
        "reproducibility_command": _sdk_reproducibility_command(
            train_path=train_path,
            val_path=val_path,
            optimizer_config_path=optimizer_config_path,
            prompt_path=prompt_path,
            output_dir=output_dir,
            update_source=update_source,
            gate_config_path=gate_config_path,
            target_prompt_paths=target_prompt_paths,
            sdk_call_agent=sdk_call_agent,
            run_id=run_id,
        ),
    }
    run = {
        "run_id": effective_run_id,
        "mode": "sdk",
        "fake_model": False,
        "fake_judge": False,
        "trace_enabled": trace,
        "train_cases": train_case_count,
        "validation_cases": validation_case_count,
        "update_source": update_source,
        "sdk_artifact_dir": audit["sdk_artifact_dir"],
        "sdk_availability": availability,
        "wrapper_gate_config_path": str(gate_config_path) if gate_config_path else None,
        "reproducibility_command": audit["reproducibility_command"],
        "paths": {
            "train": str(train_path),
            "validation": str(val_path),
            "optimizer": str(optimizer_config_path),
            "prompt": str(prompt_path),
        },
        "target_prompts": {name: str(path) for name, path in target_prompt_paths.items()},
        "prompt_source": str(prompt_path),
    }
    gate_decisions = [
        _sdk_gate_decision(
            candidate_id=candidate.candidate_id,
            sdk_summary=sdk_summary,
            gate_config=gate_config,
        )
        for candidate in candidates
    ]
    selected_candidate = None
    if candidates and gate_decisions and gate_decisions[0].accepted:
        selected_candidate = candidates[0].candidate_id
    return build_report(
        run=run,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        per_case_deltas=[],
        gate_decisions=gate_decisions,
        selected_candidate=selected_candidate,
        audit=audit,
    )


def _sdk_reproducibility_command(
    *,
    train_path: str | Path,
    val_path: str | Path,
    optimizer_config_path: str | Path,
    prompt_path: str | Path,
    output_dir: str | Path,
    update_source: bool,
    gate_config_path: str | Path | None,
    target_prompt_paths: dict[str, str | Path],
    sdk_call_agent: str | None,
    run_id: str | None,
) -> str:
    parts = [
        "python",
        "examples/optimization/eval_optimize_loop/run_pipeline.py",
        "--mode",
        "sdk",
        "--train",
        str(train_path),
        "--val",
        str(val_path),
        "--optimizer-config",
        str(optimizer_config_path),
        "--prompt",
        str(prompt_path),
        "--output-dir",
        str(output_dir),
        "--sdk-call-agent",
        sdk_call_agent or "",
    ]
    for name, path in target_prompt_paths.items():
        if not (name == "system_prompt" and Path(path) == Path(prompt_path) and len(target_prompt_paths) == 1):
            parts.extend(["--target-prompt", f"{name}={path}"])
    if gate_config_path:
        parts.extend(["--gate-config", str(gate_config_path)])
    if run_id:
        parts.extend(["--run-id", run_id])
    if update_source:
        parts.append("--update-source")
    return " ".join(shlex.quote(part) for part in parts)


def _sdk_gate_decision(
    *,
    candidate_id: str,
    sdk_summary: dict[str, Any],
    gate_config: dict[str, float],
) -> GateDecision:
    status = str(sdk_summary.get("status") or "UNKNOWN")
    improvement = _summary_float(sdk_summary, "pass_rate_improvement", 0.0, required=True)
    total_cost = _summary_float(sdk_summary, "total_llm_cost", 0.0, required=True)
    min_improvement = gate_config["min_val_score_improvement"]
    max_cost = gate_config["max_total_cost"]

    reasons: list[str] = []
    accepted = True
    if status != "SUCCEEDED":
        accepted = False
        reasons.append(f"reject: SDK optimizer status {status} is not SUCCEEDED")
    else:
        reasons.append("accept: SDK optimizer status is SUCCEEDED")

    if improvement < min_improvement:
        accepted = False
        reasons.append(
            f"reject: validation improvement {improvement:.3f} is below threshold {min_improvement:.3f}"
        )
    else:
        reasons.append(
            f"accept: validation improvement {improvement:.3f} meets threshold {min_improvement:.3f}"
        )

    if total_cost > max_cost:
        accepted = False
        reasons.append(f"reject: total SDK cost {total_cost:.3f} exceeds budget {max_cost:.3f}")
    else:
        reasons.append(f"accept: total SDK cost {total_cost:.3f} is within budget {max_cost:.3f}")

    if accepted:
        reasons.append("accept: SDK aggregate gate passed")

    return GateDecision(
        candidate_id=candidate_id,
        accepted=accepted,
        reasons=reasons,
        train_score_delta=0.0,
        validation_score_delta=improvement,
        new_hard_failures=[],
        protected_regressions=[],
        validation_new_failures=[],
        excessive_score_drops=[],
        overfit_detected=False,
        candidate_cost=total_cost,
        cumulative_cost=0.0,
        total_run_cost=total_cost,
        cost=total_cost,
        gate_status="partial_applied",
        gate_not_applied_reason="SDK OptimizeResult exposes aggregate scores but not full per-case deltas",
        not_applied_checks=[
            "per_case_delta",
            "protected_regression",
            "new_hard_failure",
            "max_score_drop_per_case",
        ],
    )


def _load_sdk_gate_config(gate_config_path: str | Path | None) -> dict[str, float]:
    if gate_config_path is None:
        gate_payload: dict[str, Any] = {}
        path_text = "--gate-config"
    else:
        payload = _read_json_object_for_audit(gate_config_path)
        gate_payload = payload.get("gate", payload)
        path_text = str(gate_config_path)
    if gate_payload is None:
        gate_payload = {}
    if not isinstance(gate_payload, dict):
        raise ValueError(f"{path_text}: field 'gate' must be an object when present")

    min_improvement = gate_payload.get("min_val_score_improvement", 0.01)
    max_cost = gate_payload.get("max_total_cost", 1.0)
    if not _is_non_negative_finite_number(min_improvement):
        raise ValueError(
            f"--gate-config {path_text}: field 'gate.min_val_score_improvement' "
            "must be a non-negative finite number"
        )
    if not _is_non_negative_finite_number(max_cost):
        raise ValueError(
            f"--gate-config {path_text}: field 'gate.max_total_cost' must be a non-negative finite number"
        )
    return {
        "min_val_score_improvement": float(min_improvement),
        "max_total_cost": float(max_cost),
    }


def _is_non_negative_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def _summary_float(summary: dict[str, Any], key: str, default: float, *, required: bool = False) -> float:
    value = summary.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        if required:
            raise ValueError(f"SDK OptimizeResult field {key} must be a finite number")
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        if required:
            raise ValueError(f"SDK OptimizeResult field {key} must be a finite number")
        return default
    if not math.isfinite(parsed):
        if required:
            raise ValueError(f"SDK OptimizeResult field {key} must be a finite number")
        return default
    return parsed


def _default_sdk_run_id(sdk_summary: dict[str, Any]) -> str:
    started_at = sdk_summary.get("started_at")
    if isinstance(started_at, str) and started_at.strip():
        source = started_at.strip()
        try:
            normalized = source[:-1] + "+00:00" if source.endswith("Z") else source
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return "eval_optimize_loop_sdk_" + parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            pass
    else:
        return "eval_optimize_loop_sdk_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = []
    for char in source:
        if char.isalnum() or char in {"-", "_"}:
            safe.append(char)
        else:
            safe.append("-")
    return "eval_optimize_loop_sdk_" + "".join(safe).strip("-")


def _parse_target_prompt_paths(
    target_prompts: list[str] | None,
    *,
    default_prompt_path: str | Path,
) -> dict[str, str | Path]:
    if not target_prompts:
        return {"system_prompt": default_prompt_path}
    parsed: dict[str, str | Path] = {}
    for item in target_prompts:
        if "=" not in item:
            raise ValueError("--target-prompt must use name=path format")
        name, path = item.split("=", 1)
        path = path.strip()
        if not TARGET_PROMPT_FIELD_RE.fullmatch(name):
            raise ValueError(
                f"--target-prompt field name {name!r} is invalid; use /^[A-Za-z_][A-Za-z0-9_]*$/"
            )
        if not path:
            raise ValueError("--target-prompt must use non-empty name=path values")
        if name in parsed:
            raise ValueError(f"--target-prompt duplicate field name {name!r}")
        parsed[name] = Path(path)
    return parsed


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str):
        raise ValueError(f"--run-id value {run_id!r} must be a string")
    if run_id in {"", ".", ".."} or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"--run-id value {run_id!r} is invalid")
    return run_id


def _resolve_default_sdk_run_id_collision(report: OptimizationReport, output_dir: str | Path) -> None:
    base_run_id = str(report.run.get("run_id") or "")
    validate_run_id(base_run_id)
    run_root = Path(output_dir) / "runs"
    candidate = base_run_id
    suffix = 1
    while (run_root / candidate).exists():
        candidate = f"{base_run_id}-{suffix}"
        suffix += 1
    report.run["run_id"] = candidate


def _candidate_prompt_hashes_by_field(
    candidates: list[CandidatePrompt],
    sdk_summary: dict[str, Any],
) -> dict[str, dict[str, str]]:
    best_prompts = sdk_summary.get("best_prompts")
    if not isinstance(best_prompts, dict):
        return {}
    return {
        candidate.candidate_id: {
            str(name): hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
            for name, prompt in best_prompts.items()
        }
        for candidate in candidates
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default=str(DEFAULT_TRAIN), help="Path to train.evalset.json")
    parser.add_argument("--val", default=str(DEFAULT_VAL), help="Path to val.evalset.json")
    parser.add_argument("--optimizer-config", default=str(DEFAULT_OPTIMIZER_CONFIG), help="Path to optimizer.json")
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="Path to baseline system prompt")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for runtime reports")
    parser.add_argument("--mode", choices=("fake", "sdk"), default="fake", help="Backend mode")
    parser.add_argument("--fake-model", action="store_true", help="Use deterministic fake model")
    parser.add_argument("--fake-judge", action="store_true", help="Use deterministic fake judge")
    parser.add_argument("--trace", action="store_true", help="Persist fake model/judge trace details per case")
    parser.add_argument("--sdk-call-agent", help="Async call_agent target for SDK mode, as module:function")
    parser.add_argument("--update-source", action="store_true", help="Allow SDK optimizer to write back source prompt")
    parser.add_argument("--gate-config", help="Wrapper gate config for SDK mode; separate from SDK optimizer config")
    parser.add_argument(
        "--target-prompt",
        action="append",
        help="SDK target prompt path in name=path format. May be repeated. Defaults to system_prompt=--prompt.",
    )
    parser.add_argument("--run-id", help="Optional report/audit run id. Fake mode keeps its deterministic default.")
    return parser.parse_args(argv)


def _input_hashes(
    *,
    train_path: str | Path,
    val_path: str | Path,
    optimizer_config_path: str | Path,
    prompt_path: str | Path,
) -> dict[str, str]:
    return {
        "train": sha256_file(train_path),
        "validation": sha256_file(val_path),
        "optimizer": sha256_file(optimizer_config_path),
        "prompt": sha256_file(prompt_path),
    }


def _count_cases(path: str | Path) -> int | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    for key in ("cases", "eval_cases"):
        cases = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(cases, list):
            return len(cases)
    return None


def _read_json_object_for_audit(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: optimizer config must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> OptimizationReport:
    args = parse_args(argv)
    report = run_pipeline(
        train_path=args.train,
        val_path=args.val,
        optimizer_config_path=args.optimizer_config,
        prompt_path=args.prompt,
        output_dir=args.output_dir,
        mode=args.mode,
        fake_model=args.fake_model or args.mode == "fake",
        fake_judge=args.fake_judge or args.mode == "fake",
        trace=args.trace,
        sdk_call_agent=args.sdk_call_agent,
        update_source=args.update_source,
        gate_config_path=args.gate_config,
        target_prompts=args.target_prompt,
        run_id=args.run_id,
    )
    output_dir = Path(args.output_dir)
    print(f"Wrote {output_dir / 'optimization_report.json'}")
    print(f"Wrote {output_dir / 'optimization_report.md'}")
    print(f"Selected candidate: {report.selected_candidate}")
    return report


if __name__ == "__main__":
    main()
