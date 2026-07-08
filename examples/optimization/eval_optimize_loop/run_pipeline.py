# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluation -> attribution -> optimization -> validation -> audit loop.

Default usage:
    PYTHONPATH=../../.. python run_pipeline.py --mode fake

The fake mode is intentionally deterministic and does not require an API key.
It still uses AgentEvaluator for train/validation scoring, then applies the
same gate and report code that a real optimizer run uses.

Trace mode also runs without an API key. It records deterministic fake outputs
into eval_mode="trace" evalsets, then scores the recorded actual_conversation
without invoking call_agent during evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from trpc_agent_sdk.evaluation import AgentEvaluator, AgentOptimizer, TargetPrompt  # noqa: E402

from agent.agent import ROUTER_PROMPT_PATH  # noqa: E402
from agent.agent import SKILL_PROMPT_PATH  # noqa: E402
from agent.agent import SYSTEM_PROMPT_PATH  # noqa: E402
from agent.agent import call_agent  # noqa: E402
from agent.agent import normalize_json_text  # noqa: E402


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
CASE_META_PATH = _HERE / "case_meta.json"
RUNS_DIR = _HERE / "runs"


@dataclass(frozen=True)
class PromptCandidate:
    candidate_id: str
    prompts: dict[str, str]
    rationale: str
    estimated_cost_usd: float = 0.0
    token_usage: dict[str, int] | None = None
    optimizer_dir: str | None = None
    optimizer_rounds: list[dict[str, Any]] | None = None


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path | str | None) -> str | None:
    """Render stable paths in reports without leaking local absolute prefixes."""
    if path is None:
        return None
    raw = Path(path)
    try:
        return str(raw.resolve().relative_to(_HERE))
    except ValueError:
        return str(raw)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if hasattr(content, "parts"):
        parts = content.parts or []
        return "".join(getattr(part, "text", "") or "" for part in parts)
    if isinstance(content, dict):
        return "".join(part.get("text", "") for part in content.get("parts", []))
    return ""


def _load_case_inputs(evalset_path: Path) -> dict[str, dict[str, str]]:
    raw = _read_json(evalset_path)
    out: dict[str, dict[str, str]] = {}
    for case in raw["eval_cases"]:
        invocation = case["conversation"][0]
        out[case["eval_id"]] = {
            "query": _content_text(invocation["user_content"]),
            "expected": normalize_json_text(_content_text(invocation["final_response"])),
        }
    return out


def _eval_case_ids(evalset_path: Path) -> set[str]:
    raw = _read_json(evalset_path)
    return {case["eval_id"] for case in raw.get("eval_cases", [])}


def validate_inputs(
    config: dict[str, Any],
    *,
    train_path: Path = TRAIN_PATH,
    val_path: Path = VAL_PATH,
    config_path: Path = CONFIG_PATH,
    case_meta_path: Path = CASE_META_PATH,
    prompt_paths: dict[str, Path] | None = None,
) -> None:
    """Validate the reproducible inputs before spending optimizer/evaluator work."""
    prompt_paths = prompt_paths or {
        "router_prompt": ROUTER_PROMPT_PATH,
        "system_prompt": SYSTEM_PROMPT_PATH,
        "skill_prompt": SKILL_PROMPT_PATH,
    }
    required_files = {
        "train_evalset": train_path,
        "validation_evalset": val_path,
        "optimizer_config": config_path,
        "case_meta": case_meta_path,
        **prompt_paths,
    }
    missing_files = [name for name, path in required_files.items() if not path.exists()]
    if missing_files:
        raise ValueError(f"missing required input file(s): {', '.join(sorted(missing_files))}")
    if train_path.resolve() == val_path.resolve():
        raise ValueError("train and validation evalsets must be different files")

    train_case_ids = _eval_case_ids(train_path)
    val_case_ids = _eval_case_ids(val_path)
    if not train_case_ids:
        raise ValueError("train evalset must contain at least one eval case")
    if not val_case_ids:
        raise ValueError("validation evalset must contain at least one eval case")
    if len(train_case_ids) != len(_read_json(train_path).get("eval_cases", [])):
        raise ValueError("train evalset contains duplicate eval_id values")
    if len(val_case_ids) != len(_read_json(val_path).get("eval_cases", [])):
        raise ValueError("validation evalset contains duplicate eval_id values")

    evaluate_config = config.get("evaluate")
    if not isinstance(evaluate_config, dict):
        raise ValueError("optimizer.json must contain an evaluate object")
    metrics = evaluate_config.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("optimizer.json evaluate.metrics must be a non-empty list")
    if int(evaluate_config.get("num_runs", 1)) < 1:
        raise ValueError("optimizer.json evaluate.num_runs must be >= 1")

    gate_config = config.get("gate")
    if not isinstance(gate_config, dict):
        raise ValueError("optimizer.json must contain a gate object")
    required_gate_keys = {
        "min_validation_score_delta",
        "allow_new_hard_fail",
        "critical_case_ids",
        "max_cost_usd",
    }
    missing_gate_keys = required_gate_keys - set(gate_config)
    if missing_gate_keys:
        raise ValueError(f"optimizer.json gate missing key(s): {sorted(missing_gate_keys)}")
    if float(gate_config["min_validation_score_delta"]) < 0:
        raise ValueError("gate.min_validation_score_delta must be >= 0")
    if float(gate_config["max_cost_usd"]) < 0:
        raise ValueError("gate.max_cost_usd must be >= 0")
    unknown_critical_ids = sorted(set(gate_config["critical_case_ids"]) - val_case_ids)
    if unknown_critical_ids:
        raise ValueError(
            "gate.critical_case_ids must refer to validation cases; unknown: "
            + ", ".join(unknown_critical_ids)
        )
    case_parallelism = int(config.get("optimize", {}).get("eval_case_parallelism", 1))
    if case_parallelism < 1:
        raise ValueError("optimize.eval_case_parallelism must be >= 1")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _metric_details(run: Any) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for metric in run.overall_eval_metric_results:
        details.append({
            "metric_name": metric.metric_name,
            "score": metric.score,
            "threshold": metric.threshold,
            "status": metric.eval_status.name.lower(),
            "reason": metric.details.reason if metric.details else None,
        })
    return details


def _extract_case_result(
    *,
    eval_id: str,
    runs: list[Any],
    case_inputs: dict[str, dict[str, str]],
    case_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = runs[0]
    invocation_results = run.eval_metric_result_per_invocation or []
    actual = ""
    expected = case_inputs[eval_id]["expected"]
    if invocation_results:
        actual = normalize_json_text(_content_text(invocation_results[0].actual_invocation.final_response))
        if invocation_results[0].expected_invocation is not None:
            expected = normalize_json_text(_content_text(invocation_results[0].expected_invocation.final_response))

    metric_scores = [
        metric.score
        for metric in run.overall_eval_metric_results
        if metric.score is not None
    ]
    score = sum(metric_scores) / len(metric_scores) if metric_scores else 0.0
    passed = getattr(run.final_eval_status, "name", str(run.final_eval_status)) == "PASSED"
    failure_types, reason = attribute_failure(
        expected=expected,
        actual=actual,
        case_id=eval_id,
        case_meta=case_meta,
    )

    return {
        "case_id": eval_id,
        "query": case_inputs[eval_id]["query"],
        "expected": expected,
        "actual": actual,
        "score": score,
        "passed": passed,
        "status": run.final_eval_status.name.lower(),
        "metrics": _metric_details(run),
        "failure_types": [] if passed else failure_types,
        "reason": "" if passed else reason,
    }


def _summarize_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    passed = sum(1 for case in cases if case["passed"])
    overall_score = sum(float(case["score"]) for case in cases) / total if total else 0.0
    return {
        "overall_score": round(overall_score, 4),
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "cases": cases,
    }


async def evaluate_dataset(
    *,
    split: str,
    evalset_path: Path,
    metrics_path: Path,
    output_dir: Path,
    num_runs: int,
    case_parallelism: int,
    case_meta: dict[str, Any] | None = None,
    use_trace: bool = False,
) -> dict[str, Any]:
    case_inputs = _load_case_inputs(evalset_path)
    eval_output_dir = output_dir / f"eval_{split}"
    executer = AgentEvaluator.get_executer(
        str(evalset_path),
        call_agent=None if use_trace else call_agent,
        num_runs=num_runs,
        print_detailed_results=False,
        print_summary_report=False,
        eval_result_output_dir=str(eval_output_dir),
        eval_metrics_file_path_or_dir=str(metrics_path),
        case_parallelism=case_parallelism,
        case_eval_parallelism=case_parallelism,
    )
    try:
        await executer.evaluate()
    except AssertionError:
        if executer.get_result() is None:
            raise
    result = executer.get_result()
    if result is None:
        raise RuntimeError(f"AgentEvaluator did not return a result for {split}")

    cases: list[dict[str, Any]] = []
    for set_result in result.results_by_eval_set_id.values():
        for eval_id, runs in sorted(set_result.eval_results_by_eval_id.items()):
            cases.append(
                _extract_case_result(
                    eval_id=eval_id,
                    runs=runs,
                    case_inputs=case_inputs,
                    case_meta=case_meta,
                )
            )
    summary = _summarize_cases(cases)
    summary["split"] = split
    return summary


async def write_trace_evalset(*, source_path: Path, target_path: Path) -> Path:
    """Materialize a trace evalset from the current prompt behavior."""
    raw = _read_json(source_path)
    traced_cases: list[dict[str, Any]] = []
    for case in raw["eval_cases"]:
        expected_invocation = case["conversation"][0]
        query = _content_text(expected_invocation["user_content"])
        actual = await call_agent(query)
        traced = dict(case)
        traced["eval_mode"] = "trace"
        traced["actual_conversation"] = [{
            "invocation_id": f"{expected_invocation.get('invocation_id', case['eval_id'])}-actual",
            "user_content": expected_invocation["user_content"],
            "final_response": {
                "parts": [{
                    "text": actual
                }],
                "role": "model",
            },
        }]
        traced_cases.append(traced)
    payload = dict(raw)
    payload["eval_set_id"] = f"{raw['eval_set_id']}_{target_path.stem}"
    payload["description"] = f"Trace replay generated from {source_path.name}."
    payload["eval_cases"] = traced_cases
    _write_json(target_path, payload)
    return target_path


def attribute_failure(
    *,
    expected: str,
    actual: str,
    case_id: str | None = None,
    case_meta: dict[str, Any] | None = None,
) -> tuple[list[str], str]:
    expected_obj = _parse_json_object(expected)
    actual_obj = _parse_json_object(actual)
    hinted_types = []
    if case_id and case_meta:
        hinted_types = list(case_meta.get(case_id, {}).get("fake_attribution_hints", []))
    if expected_obj is None or actual_obj is None:
        failure_types = sorted(set(["format_violation", *hinted_types]))
        return failure_types, "Final response is not valid normalized JSON."

    failure_types: list[str] = []
    reasons: list[str] = []
    for key in ("category", "priority", "action"):
        if key not in actual_obj:
            failure_types.append("format_violation")
            reasons.append(f"missing key {key}")

    if expected_obj.get("category") != actual_obj.get("category"):
        failure_types.append("knowledge_recall_insufficient")
        reasons.append(f"category expected {expected_obj.get('category')} but got {actual_obj.get('category')}")
    if expected_obj.get("priority") != actual_obj.get("priority"):
        failure_types.append("parameter_error")
        reasons.append(f"priority expected {expected_obj.get('priority')} but got {actual_obj.get('priority')}")
    if expected_obj.get("action") != actual_obj.get("action"):
        failure_types.append("final_response_mismatch")
        reasons.append(f"action expected {expected_obj.get('action')} but got {actual_obj.get('action')}")

    if not failure_types:
        failure_types.append("final_response_mismatch")
        reasons.append("final response differs from reference")
    if hinted_types:
        failure_types.extend(hinted_types)
        reasons.append(
            "deterministic attribution hint: "
            + ", ".join(sorted(set(hinted_types)))
        )
    return sorted(set(failure_types)), "; ".join(reasons)


def build_failure_stats(*summaries: dict[str, Any]) -> dict[str, Any]:
    stats: dict[str, int] = {}
    cases: list[dict[str, Any]] = []
    for summary in summaries:
        for case in summary["cases"]:
            if case["passed"]:
                continue
            for failure_type in case["failure_types"]:
                stats[failure_type] = stats.get(failure_type, 0) + 1
            cases.append({
                "split": summary["split"],
                "case_id": case["case_id"],
                "failure_types": case["failure_types"],
                "reason": case["reason"],
            })
    return {
        "stats": dict(sorted(stats.items())),
        "failed_cases": cases,
    }


def build_attribution_self_check(failure_attribution: dict[str, Any], case_meta: dict[str, Any]) -> dict[str, Any]:
    by_case: dict[str, Any] = {}
    matched = 0
    labeled = 0
    hint_assisted = 0
    rule_only_labeled = 0
    rule_only_matched = 0
    for failed_case in failure_attribution["failed_cases"]:
        case_id = failed_case["case_id"]
        meta = case_meta.get(case_id, {})
        expected = set(meta.get("expected_failure_types", []))
        if not expected:
            continue
        actual = set(failed_case["failure_types"])
        hints = set(meta.get("fake_attribution_hints", []))
        is_match = bool(expected & actual)
        is_hint_assisted = bool(expected & actual & hints)
        labeled += 1
        matched += 1 if is_match else 0
        hint_assisted += 1 if is_hint_assisted else 0
        if not is_hint_assisted:
            rule_only_labeled += 1
            rule_only_matched += 1 if is_match else 0
        by_case[case_id] = {
            "expected_failure_types": sorted(expected),
            "actual_failure_types": sorted(actual),
            "fake_attribution_hints": sorted(hints),
            "hint_assisted": is_hint_assisted,
            "matched": is_match,
            "note": case_meta.get(case_id, {}).get("note", ""),
        }
    return {
        "labeled_failed_cases": labeled,
        "matched": matched,
        "accuracy": round(matched / labeled, 4) if labeled else None,
        "hint_assisted_cases": hint_assisted,
        "rule_only_labeled_cases": rule_only_labeled,
        "rule_only_matched": rule_only_matched,
        "rule_only_accuracy": round(rule_only_matched / rule_only_labeled, 4) if rule_only_labeled else None,
        "by_case": by_case,
    }


@asynccontextmanager
async def temporary_prompts(target: TargetPrompt, prompts: dict[str, str]):
    baseline = await target.read_all()
    await target.write_all(prompts)
    try:
        yield
    finally:
        await target.write_all(baseline)


def build_fake_candidate(config: dict[str, Any], baseline_prompts: dict[str, str], scenario: str) -> PromptCandidate:
    fake_config = config.get("fake_optimizer", {})
    scenario_note = (
        "- overfit_payment_outage: route payment outage language through the billing refund path.\n"
        if scenario == "overfit" else
        "- Preserve production outage handling as technical p1 escalation.\n"
    )
    candidate_prompts = dict(baseline_prompts)
    candidate_prompts["router_prompt"] = baseline_prompts["router_prompt"].rstrip() + f"""

Candidate routing update:
- Route double charge, refund, and VIP refund tickets to billing handling.
{scenario_note}"""
    candidate_prompts["system_prompt"] = baseline_prompts["system_prompt"].rstrip() + """

Candidate system update:
- Keep the response as one compact JSON object with category, priority, and action.
- Do not add prose outside the JSON object.
"""
    candidate_prompts["skill_prompt"] = baseline_prompts["skill_prompt"].rstrip() + f"""

Optimization candidate notes:
- Treat double charge, refund, and VIP refund requests as billing issues.
- For refund requests, choose action refund_review.
- For VIP refund requests, use priority p1.
- scenario: {scenario}
"""
    candidate_id = (
        fake_config.get("candidate_id", "candidate_fake_refund_rule")
        if scenario == "overfit" else
        f"candidate_refund_rule_{scenario}"
    )
    rationale = (
        fake_config.get("notes", "Deterministic fake optimizer candidate.")
        if scenario == "overfit" else
        "Adds refund handling while preserving production outage priority, so validation improves without critical regressions."
    )
    max_cost = float(config.get("gate", {}).get("max_cost_usd", 0.01))
    estimated_cost = max_cost + 0.01 if scenario == "cost_exceeded" else 0.0
    token_usage = (
        {"prompt": 512, "completion": 128, "total": 640}
        if scenario == "cost_exceeded" else
        {"prompt": 0, "completion": 0, "total": 0}
    )
    return PromptCandidate(
        candidate_id=candidate_id,
        prompts=candidate_prompts,
        rationale=rationale,
        estimated_cost_usd=estimated_cost,
        token_usage=token_usage,
        optimizer_dir=None,
        optimizer_rounds=[],
    )


async def build_real_candidate(
    *,
    config_path: Path,
    target: TargetPrompt,
    train_path: Path,
    val_path: Path,
    output_dir: Path,
) -> PromptCandidate:
    optimizer_dir = output_dir / "agent_optimizer"
    result = await AgentOptimizer.optimize(
        config_path=str(config_path),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(optimizer_dir),
        update_source=False,
        verbose=1,
    )
    return PromptCandidate(
        candidate_id="agent_optimizer_best",
        prompts=result.best_prompts,
        rationale=f"AgentOptimizer status={result.status}, finish_reason={result.finish_reason}",
        estimated_cost_usd=result.total_llm_cost,
        token_usage=result.total_token_usage,
        optimizer_dir=str(optimizer_dir),
        optimizer_rounds=[
            {
                "round": record.round,
                "kind": record.kind,
                "optimized_field_names": record.optimized_field_names,
                "candidate_prompts": record.candidate_prompts,
                "evaluation_results": {
                    "validation_pass_rate": record.validation_pass_rate,
                    "metric_breakdown": record.metric_breakdown,
                    "failed_case_ids": record.failed_case_ids,
                },
                "validation_pass_rate": record.validation_pass_rate,
                "metric_breakdown": record.metric_breakdown,
                "accepted": record.accepted,
                "acceptance_reason": record.acceptance_reason,
                "failed_case_ids": record.failed_case_ids,
                "cost_usd": record.round_llm_cost,
                "duration_seconds": record.duration_seconds,
            }
            for record in result.rounds
        ],
    )


def build_case_deltas(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_by_id = {case["case_id"]: case for case in candidate["cases"]}
    deltas: list[dict[str, Any]] = []
    for base_case in baseline["cases"]:
        cand_case = candidate_by_id[base_case["case_id"]]
        score_delta = round(float(cand_case["score"]) - float(base_case["score"]), 4)
        if not base_case["passed"] and cand_case["passed"]:
            outcome = "new_pass"
        elif base_case["passed"] and not cand_case["passed"]:
            outcome = "new_fail"
        elif score_delta > 0:
            outcome = "score_improved"
        elif score_delta < 0:
            outcome = "score_regressed"
        else:
            outcome = "unchanged"
        deltas.append({
            "case_id": base_case["case_id"],
            "baseline_score": base_case["score"],
            "candidate_score": cand_case["score"],
            "score_delta": score_delta,
            "baseline_passed": base_case["passed"],
            "candidate_passed": cand_case["passed"],
            "outcome": outcome,
            "baseline_actual": base_case["actual"],
            "candidate_actual": cand_case["actual"],
            "candidate_failure_types": cand_case["failure_types"],
            "candidate_reason": cand_case["reason"],
        })
    return deltas


def apply_gate(
    *,
    gate_config: dict[str, Any],
    train_delta: float,
    validation_delta: float,
    validation_case_deltas: list[dict[str, Any]],
    cost_usd: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    checks: list[dict[str, Any]] = []
    min_delta = float(gate_config.get("min_validation_score_delta", 0.0))
    allow_new_hard_fail = bool(gate_config.get("allow_new_hard_fail", False))
    critical_ids = set(gate_config.get("critical_case_ids", []))
    max_cost_usd = float(gate_config.get("max_cost_usd", float("inf")))

    min_delta_passed = validation_delta >= min_delta
    checks.append({
        "name": "min_validation_score_delta",
        "passed": min_delta_passed,
        "actual": validation_delta,
        "expected": min_delta,
    })
    if not min_delta_passed:
        reasons.append(
            f"validation score delta {validation_delta:+.4f} is below required {min_delta:+.4f}"
        )
    new_failures = [delta["case_id"] for delta in validation_case_deltas if delta["outcome"] == "new_fail"]
    no_new_hard_fail_passed = allow_new_hard_fail or not new_failures
    checks.append({
        "name": "no_new_hard_fail",
        "passed": no_new_hard_fail_passed,
        "case_ids": new_failures,
        "allow_new_hard_fail": allow_new_hard_fail,
    })
    if not no_new_hard_fail_passed:
        reasons.append(f"new hard fail(s) are not allowed: {', '.join(new_failures)}")
    critical_regressions = [
        delta["case_id"]
        for delta in validation_case_deltas
        if delta["case_id"] in critical_ids and delta["candidate_score"] < delta["baseline_score"]
    ]
    critical_passed = not critical_regressions
    checks.append({
        "name": "critical_case_regression",
        "passed": critical_passed,
        "case_ids": critical_regressions,
        "critical_case_ids": sorted(critical_ids),
    })
    if not critical_passed:
        reasons.append(f"critical case regression(s): {', '.join(critical_regressions)}")
    overfitting_triggered = train_delta > 0 and validation_delta <= 0
    checks.append({
        "name": "overfitting_guard",
        "passed": not overfitting_triggered,
        "train_score_delta": train_delta,
        "validation_score_delta": validation_delta,
    })
    if overfitting_triggered:
        reasons.append("overfitting guard triggered: train improved while validation did not improve")
    cost_passed = cost_usd <= max_cost_usd
    checks.append({
        "name": "max_cost_usd",
        "passed": cost_passed,
        "actual": cost_usd,
        "expected": max_cost_usd,
    })
    if not cost_passed:
        reasons.append(f"cost ${cost_usd:.4f} exceeds budget ${max_cost_usd:.4f}")

    return {
        "decision": "accepted" if not reasons else "rejected",
        "reasons": reasons or ["all configured gate conditions passed"],
        "checks": checks,
        "config": gate_config,
        "overfitting_guard_triggered": overfitting_triggered,
    }


def build_optimization_rounds(
    *,
    mode: str,
    baseline_prompts: dict[str, str],
    candidate: PromptCandidate,
    baseline_train: dict[str, Any],
    baseline_validation: dict[str, Any],
    candidate_train: dict[str, Any],
    candidate_validation: dict[str, Any],
    gate: dict[str, Any],
) -> list[dict[str, Any]]:
    if mode == "optimizer" and candidate.optimizer_rounds:
        return candidate.optimizer_rounds
    return [
        {
            "round": 0,
            "kind": "baseline",
            "optimized_field_names": [],
            "candidate_id": "baseline",
            "candidate_prompts": baseline_prompts,
            "evaluation_results": {
                "train": {
                    "overall_score": baseline_train["overall_score"],
                    "pass_rate": baseline_train["pass_rate"],
                    "passed": baseline_train["passed"],
                    "failed": baseline_train["failed"],
                    "total": baseline_train["total"],
                },
                "validation": {
                    "overall_score": baseline_validation["overall_score"],
                    "pass_rate": baseline_validation["pass_rate"],
                    "passed": baseline_validation["passed"],
                    "failed": baseline_validation["failed"],
                    "total": baseline_validation["total"],
                },
            },
            "evaluation_result_refs": {
                "train": "eval_train_baseline",
                "validation": "eval_validation_baseline",
            },
            "train_score": baseline_train["overall_score"],
            "validation_score": baseline_validation["overall_score"],
            "accepted": True,
            "acceptance_reason": "Initial prompt used as the control arm.",
            "cost_usd": 0.0,
            "duration_seconds": 0.0,
        },
        {
            "round": 1,
            "kind": "fake_reflective",
            "optimized_field_names": list(candidate.prompts.keys()),
            "candidate_id": candidate.candidate_id,
            "candidate_prompts": candidate.prompts,
            "evaluation_results": {
                "train": {
                    "overall_score": candidate_train["overall_score"],
                    "pass_rate": candidate_train["pass_rate"],
                    "passed": candidate_train["passed"],
                    "failed": candidate_train["failed"],
                    "total": candidate_train["total"],
                },
                "validation": {
                    "overall_score": candidate_validation["overall_score"],
                    "pass_rate": candidate_validation["pass_rate"],
                    "passed": candidate_validation["passed"],
                    "failed": candidate_validation["failed"],
                    "total": candidate_validation["total"],
                },
            },
            "evaluation_result_refs": {
                "train": "eval_train_candidate",
                "validation": "eval_validation_candidate",
            },
            "train_score": candidate_train["overall_score"],
            "validation_score": candidate_validation["overall_score"],
            "accepted": gate["decision"] == "accepted",
            "acceptance_reason": "; ".join(gate["reasons"]),
            "cost_usd": candidate.estimated_cost_usd,
            "duration_seconds": 0.0,
        },
    ]


def build_prompt_audit(
    *,
    baseline_prompts: dict[str, str],
    candidate_prompts: dict[str, str],
    prompt_sources: dict[str, Path],
) -> dict[str, dict[str, Any]]:
    audit: dict[str, dict[str, Any]] = {}
    for name, baseline in baseline_prompts.items():
        candidate = candidate_prompts.get(name, baseline)
        diff_lines = list(difflib.unified_diff(
            baseline.splitlines(),
            candidate.splitlines(),
            fromfile=f"baseline/{name}",
            tofile=f"candidate/{name}",
            lineterm="",
        ))
        audit[name] = {
            "source": _display_path(prompt_sources.get(name)),
            "baseline_sha256": _sha256_text(baseline),
            "candidate_sha256": _sha256_text(candidate),
            "changed": baseline != candidate,
            "baseline_chars": len(baseline),
            "candidate_chars": len(candidate),
            "diff": {
                "line_count": len(diff_lines),
                "truncated": len(diff_lines) > 40,
                "preview": diff_lines[:40],
            },
        }
    return audit


def build_input_audit(*, prompt_sources: dict[str, Path]) -> dict[str, Any]:
    input_files = {
        "train_evalset": TRAIN_PATH,
        "validation_evalset": VAL_PATH,
        "optimizer_config": CONFIG_PATH,
        "case_meta": CASE_META_PATH,
    }
    return {
        "files": {
            name: {
                "path": _display_path(path),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for name, path in input_files.items()
        },
        "prompt_sources": {
            name: {
                "path": _display_path(path),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for name, path in prompt_sources.items()
        },
    }


def build_markdown(report: dict[str, Any]) -> str:
    baseline_val = report["baseline"]["validation"]
    candidate_val = report["candidate"]["validation"]
    gate = report["gate"]
    decision_zh = "接受" if gate["decision"] == "accepted" else "拒绝"

    lines = [
        "# 优化报告",
        "",
        f"决策：**{decision_zh} ({gate['decision'].upper()})**",
        "",
        "## 分数",
        "",
        "| 数据集 | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for split in ("train", "validation"):
        b = report["baseline"][split]["overall_score"]
        c = report["candidate"][split]["overall_score"]
        d = report["delta"][f"{split}_score_delta"]
        split_name = "训练集" if split == "train" else "验证集"
        lines.append(f"| {split_name} | {b:.4f} | {c:.4f} | {d:+.4f} |")

    lines.extend([
        "",
        "## Gate 原因",
        "",
    ])
    lines.extend(f"- {reason}" for reason in gate["reasons"])

    lines.extend([
        "",
        "## 验证集 Case Delta",
        "",
        "| Case | Baseline | Candidate | Delta | Outcome |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for delta in report["delta"]["validation_case_deltas"]:
        lines.append(
            f"| {delta['case_id']} | {delta['baseline_score']:.4f} | "
            f"{delta['candidate_score']:.4f} | {delta['score_delta']:+.4f} | {delta['outcome']} |"
        )

    lines.extend([
        "",
        "## 失败归因",
        "",
    ])
    for failure_type, count in report["failure_attribution"]["stats"].items():
        lines.append(f"- {failure_type}: {count}")
    if not report["failure_attribution"]["stats"]:
        lines.append("- 没有失败 case")
    self_check = report["failure_attribution"].get("self_check", {})
    if self_check:
        lines.append(
            f"- self-check 准确率：{self_check.get('accuracy')} "
            f"({self_check.get('matched')}/{self_check.get('labeled_failed_cases')})"
        )
        lines.append(
            f"- hint-assisted cases: {self_check.get('hint_assisted_cases')} "
            f"| rule-only accuracy: {self_check.get('rule_only_accuracy')}"
        )

    lines.extend([
        "",
        "## 候选 Prompt",
        "",
        f"- candidate_id: `{report['candidate']['candidate_id']}`",
        f"- rationale: {report['candidate']['rationale']}",
        f"- validation pass rate: {candidate_val['pass_rate']:.4f}",
        f"- baseline validation pass rate: {baseline_val['pass_rate']:.4f}",
        f"- audited rounds: {len(report['optimization_rounds'])}",
        "",
        "## Prompt 审计",
        "",
    ])
    for name, audit in report["prompt_audit"].items():
        lines.append(
            f"- `{name}`: changed={audit['changed']} "
            f"baseline={audit['baseline_sha256'][:12]} "
            f"candidate={audit['candidate_sha256'][:12]} "
            f"diff_lines={audit['diff']['line_count']}"
        )

    lines.extend([
        "",
        "## 输入审计",
        "",
    ])
    for name, audit in report["input_audit"]["files"].items():
        lines.append(f"- `{name}`: {audit['sha256'][:12]} ({audit['bytes']} bytes)")

    lines.extend([
        "",
        "## 复现命令",
        "",
        "```bash",
        "cd examples/optimization/eval_optimize_loop",
        "PYTHONPATH=../../.. python run_pipeline.py --mode fake",
        "```",
        "",
        "# Optimization Report",
        "",
        f"Decision: **{gate['decision'].upper()}**",
        "",
        "## Scores",
        "",
        "| Split | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ])
    for split in ("train", "validation"):
        b = report["baseline"][split]["overall_score"]
        c = report["candidate"][split]["overall_score"]
        d = report["delta"][f"{split}_score_delta"]
        lines.append(f"| {split} | {b:.4f} | {c:.4f} | {d:+.4f} |")

    lines.extend([
        "",
        "## Gate Reasons",
        "",
    ])
    lines.extend(f"- {reason}" for reason in gate["reasons"])

    lines.extend([
        "",
        "## Validation Case Delta",
        "",
        "| Case | Baseline | Candidate | Delta | Outcome |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for delta in report["delta"]["validation_case_deltas"]:
        lines.append(
            f"| {delta['case_id']} | {delta['baseline_score']:.4f} | "
            f"{delta['candidate_score']:.4f} | {delta['score_delta']:+.4f} | {delta['outcome']} |"
        )

    lines.extend([
        "",
        "## Failure Attribution",
        "",
    ])
    for failure_type, count in report["failure_attribution"]["stats"].items():
        lines.append(f"- {failure_type}: {count}")
    if not report["failure_attribution"]["stats"]:
        lines.append("- no failed cases")
    self_check = report["failure_attribution"].get("self_check", {})
    if self_check:
        lines.append(
            f"- self-check accuracy: {self_check.get('accuracy')} "
            f"({self_check.get('matched')}/{self_check.get('labeled_failed_cases')})"
        )
        lines.append(
            f"- hint-assisted cases: {self_check.get('hint_assisted_cases')} "
            f"| rule-only accuracy: {self_check.get('rule_only_accuracy')}"
        )

    lines.extend([
        "",
        "## Candidate",
        "",
        f"- candidate_id: `{report['candidate']['candidate_id']}`",
        f"- rationale: {report['candidate']['rationale']}",
        f"- validation pass rate: {candidate_val['pass_rate']:.4f}",
        f"- baseline validation pass rate: {baseline_val['pass_rate']:.4f}",
        f"- audited rounds: {len(report['optimization_rounds'])}",
        "",
        "## Prompt Audit",
        "",
    ])
    for name, audit in report["prompt_audit"].items():
        lines.append(
            f"- `{name}`: changed={audit['changed']} "
            f"baseline={audit['baseline_sha256'][:12]} "
            f"candidate={audit['candidate_sha256'][:12]} "
            f"diff_lines={audit['diff']['line_count']}"
        )

    lines.extend([
        "",
        "## Input Audit",
        "",
    ])
    for name, audit in report["input_audit"]["files"].items():
        lines.append(f"- `{name}`: {audit['sha256'][:12]} ({audit['bytes']} bytes)")

    lines.extend([
        "",
        "## Reproduce",
        "",
        "```bash",
        "cd examples/optimization/eval_optimize_loop",
        "PYTHONPATH=../../.. python run_pipeline.py --mode fake",
        "```",
        "",
    ])
    return "\n".join(lines)


def validate_report(report: dict[str, Any]) -> None:
    """Fail fast if a report misses fields downstream CI/review relies on."""
    required_top_level = {
        "baseline",
        "candidate",
        "delta",
        "gate",
        "input_audit",
        "prompt_audit",
        "optimization_rounds",
        "failure_attribution",
        "audit",
    }
    missing = required_top_level - set(report)
    if missing:
        raise ValueError(f"optimization report missing top-level fields: {sorted(missing)}")

    for arm in ("baseline", "candidate"):
        for split in ("train", "validation"):
            summary = report[arm][split]
            for key in ("overall_score", "pass_rate", "cases"):
                if key not in summary:
                    raise ValueError(f"{arm}.{split} missing {key}")
            for case in summary["cases"]:
                for key in ("case_id", "score", "passed", "metrics", "actual", "expected"):
                    if key not in case:
                        raise ValueError(f"{arm}.{split} case missing {key}: {case}")
                if not case["passed"] and (not case.get("failure_types") or not case.get("reason")):
                    raise ValueError(f"{arm}.{split} failed case lacks attribution: {case['case_id']}")

    validation_case_ids = {case["case_id"] for case in report["baseline"]["validation"]["cases"]}
    delta_case_ids = {case["case_id"] for case in report["delta"]["validation_case_deltas"]}
    if validation_case_ids != delta_case_ids:
        raise ValueError("validation case delta coverage does not match baseline validation cases")

    gate = report["gate"]
    if gate.get("decision") not in {"accepted", "rejected"}:
        raise ValueError(f"invalid gate decision: {gate.get('decision')}")
    if not gate.get("checks"):
        raise ValueError("gate.checks must not be empty")

    rounds = report["optimization_rounds"]
    if not rounds:
        raise ValueError("optimization_rounds must not be empty")
    for record in rounds:
        for key in ("candidate_prompts", "evaluation_results", "accepted", "acceptance_reason", "cost_usd", "duration_seconds"):
            if key not in record:
                raise ValueError(f"round record missing audit field {key}: {record}")
        if "train" not in record["evaluation_results"] and "validation_pass_rate" not in record["evaluation_results"]:
            raise ValueError(f"round record missing audit fields: {record}")

    if not report["prompt_audit"]:
        raise ValueError("prompt_audit must not be empty")
    for name, record in report["prompt_audit"].items():
        for key in ("source", "baseline_sha256", "candidate_sha256", "changed", "diff"):
            if key not in record:
                raise ValueError(f"prompt_audit[{name}] missing {key}")
        if "preview" not in record["diff"] or "line_count" not in record["diff"]:
            raise ValueError(f"prompt_audit[{name}] diff missing preview or line_count")

    input_audit = report["input_audit"]
    for section in ("files", "prompt_sources"):
        if section not in input_audit:
            raise ValueError(f"input_audit missing {section}")
        for name, record in input_audit[section].items():
            for key in ("path", "sha256", "bytes"):
                if key not in record:
                    raise ValueError(f"input_audit[{section}][{name}] missing {key}")

    self_check = report["failure_attribution"].get("self_check")
    if not self_check:
        raise ValueError("failure_attribution.self_check must not be empty")
    accuracy = self_check.get("accuracy")
    if accuracy is not None and accuracy < 0.75:
        raise ValueError(f"failure attribution self-check accuracy below 0.75: {accuracy}")


def copy_sample_outputs(output_dir: Path) -> None:
    sample_dir = _HERE / "sample_outputs"
    sample_dir.mkdir(exist_ok=True)
    report = _read_json(output_dir / "optimization_report.json")
    report["run"]["started_at"] = "2026-07-06T00:00:00"
    report["run"]["duration_seconds"] = 0.0
    report["audit"]["candidate_prompt_dir"] = "runs/<sample>/candidate_prompts"
    _write_json(sample_dir / "optimization_report.json", report)
    (sample_dir / "optimization_report.md").write_text(build_markdown(report), encoding="utf-8")


async def run_pipeline(args: argparse.Namespace) -> Path:
    started = time.perf_counter()
    config = _read_json(CONFIG_PATH)
    case_meta = _read_json(CASE_META_PATH)
    validate_inputs(config)
    num_runs = int(config.get("evaluate", {}).get("num_runs", 1))
    case_parallelism = int(config.get("optimize", {}).get("eval_case_parallelism", 1))
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_name = f"{args.mode}_{args.scenario}_{timestamp}" if args.mode != "optimizer" else f"{args.mode}_{timestamp}"
    output_dir = (Path(args.output_dir) if args.output_dir else RUNS_DIR / run_name).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    target = (
        TargetPrompt()
        .add_path("router_prompt", str(ROUTER_PROMPT_PATH))
        .add_path("system_prompt", str(SYSTEM_PROMPT_PATH))
        .add_path("skill_prompt", str(SKILL_PROMPT_PATH))
    )
    baseline_prompts = await target.read_all()
    _write_json(output_dir / "config.snapshot.json", config)
    metrics_config_path = output_dir / "eval_metrics.snapshot.json"
    _write_json(metrics_config_path, config.get("evaluate", {}))
    use_trace = args.mode == "trace"
    trace_dir = output_dir / "trace_evalsets"
    if use_trace:
        trace_dir.mkdir(exist_ok=True)
        baseline_train_path = await write_trace_evalset(
            source_path=TRAIN_PATH,
            target_path=trace_dir / "train_baseline.evalset.json",
        )
        baseline_val_path = await write_trace_evalset(
            source_path=VAL_PATH,
            target_path=trace_dir / "validation_baseline.evalset.json",
        )
    else:
        baseline_train_path = TRAIN_PATH
        baseline_val_path = VAL_PATH

    baseline_train = await evaluate_dataset(
        split="train_baseline",
        evalset_path=baseline_train_path,
        metrics_path=metrics_config_path,
        output_dir=output_dir,
        num_runs=num_runs,
        case_parallelism=case_parallelism,
        case_meta=case_meta,
        use_trace=use_trace,
    )
    baseline_val = await evaluate_dataset(
        split="validation_baseline",
        evalset_path=baseline_val_path,
        metrics_path=metrics_config_path,
        output_dir=output_dir,
        num_runs=num_runs,
        case_parallelism=case_parallelism,
        case_meta=case_meta,
        use_trace=use_trace,
    )

    baseline_attribution = build_failure_stats(baseline_train, baseline_val)
    baseline_attribution["self_check"] = build_attribution_self_check(baseline_attribution, case_meta)
    if args.mode == "optimizer":
        candidate = await build_real_candidate(
            config_path=CONFIG_PATH,
            target=target,
            train_path=TRAIN_PATH,
            val_path=VAL_PATH,
            output_dir=output_dir,
        )
    else:
        candidate = build_fake_candidate(config, baseline_prompts, args.scenario)

    candidate_prompt_dir = output_dir / "candidate_prompts"
    candidate_prompt_dir.mkdir(exist_ok=True)
    for name, content in candidate.prompts.items():
        (candidate_prompt_dir / f"{name}.md").write_text(content, encoding="utf-8")

    async with temporary_prompts(target, candidate.prompts):
        if use_trace:
            candidate_train_path = await write_trace_evalset(
                source_path=TRAIN_PATH,
                target_path=trace_dir / "train_candidate.evalset.json",
            )
            candidate_val_path = await write_trace_evalset(
                source_path=VAL_PATH,
                target_path=trace_dir / "validation_candidate.evalset.json",
            )
        else:
            candidate_train_path = TRAIN_PATH
            candidate_val_path = VAL_PATH
        candidate_train = await evaluate_dataset(
            split="train_candidate",
            evalset_path=candidate_train_path,
            metrics_path=metrics_config_path,
            output_dir=output_dir,
            num_runs=num_runs,
            case_parallelism=case_parallelism,
            case_meta=case_meta,
            use_trace=use_trace,
        )
        candidate_val = await evaluate_dataset(
            split="validation_candidate",
            evalset_path=candidate_val_path,
            metrics_path=metrics_config_path,
            output_dir=output_dir,
            num_runs=num_runs,
            case_parallelism=case_parallelism,
            case_meta=case_meta,
            use_trace=use_trace,
        )

    train_delta = round(candidate_train["overall_score"] - baseline_train["overall_score"], 4)
    validation_delta = round(candidate_val["overall_score"] - baseline_val["overall_score"], 4)
    train_case_deltas = build_case_deltas(baseline=baseline_train, candidate=candidate_train)
    validation_case_deltas = build_case_deltas(baseline=baseline_val, candidate=candidate_val)
    evaluation_cost_usd = 0.0
    optimizer_cost_usd = candidate.estimated_cost_usd
    total_cost_usd = round(optimizer_cost_usd + evaluation_cost_usd, 6)
    gate = apply_gate(
        gate_config=config.get("gate", {}),
        train_delta=train_delta,
        validation_delta=validation_delta,
        validation_case_deltas=validation_case_deltas,
        cost_usd=total_cost_usd,
    )
    optimization_rounds = build_optimization_rounds(
        mode=args.mode,
        baseline_prompts=baseline_prompts,
        candidate=candidate,
        baseline_train=baseline_train,
        baseline_validation=baseline_val,
        candidate_train=candidate_train,
        candidate_validation=candidate_val,
        gate=gate,
    )
    prompt_sources = {
        "router_prompt": ROUTER_PROMPT_PATH,
        "system_prompt": SYSTEM_PROMPT_PATH,
        "skill_prompt": SKILL_PROMPT_PATH,
    }
    prompt_audit = build_prompt_audit(
        baseline_prompts=baseline_prompts,
        candidate_prompts=candidate.prompts,
        prompt_sources=prompt_sources,
    )
    input_audit = build_input_audit(prompt_sources=prompt_sources)

    report = {
        "schema_version": "v1",
        "run": {
            "mode": args.mode,
            "scenario": None if args.mode == "optimizer" else args.scenario,
            "judge_mode": "local_exact_match_fake_judge",
            "seed": config.get("fake_optimizer", {}).get("seed", config.get("optimize", {}).get("algorithm", {}).get("seed")),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(time.perf_counter() - started, 4),
        },
        "inputs": {
            "train_evalset": _display_path(TRAIN_PATH),
            "validation_evalset": _display_path(VAL_PATH),
            "optimizer_config": _display_path(CONFIG_PATH),
            "case_meta": _display_path(CASE_META_PATH),
            "prompt_sources": {
                "router_prompt": _display_path(ROUTER_PROMPT_PATH),
                "system_prompt": _display_path(SYSTEM_PROMPT_PATH),
                "skill_prompt": _display_path(SKILL_PROMPT_PATH),
            },
            "trace_evalsets": _display_path(trace_dir) if use_trace else None,
        },
        "baseline": {
            "prompts": baseline_prompts,
            "train": baseline_train,
            "validation": baseline_val,
        },
        "candidate": {
            "candidate_id": candidate.candidate_id,
            "rationale": candidate.rationale,
            "prompts": candidate.prompts,
            "train": candidate_train,
            "validation": candidate_val,
        },
        "delta": {
            "train_score_delta": train_delta,
            "validation_score_delta": validation_delta,
            "train_case_deltas": train_case_deltas,
            "validation_case_deltas": validation_case_deltas,
        },
        "gate": gate,
        "input_audit": input_audit,
        "prompt_audit": prompt_audit,
        "optimization_rounds": optimization_rounds,
        "failure_attribution": baseline_attribution,
        "audit": {
            "candidate_prompt_dir": _display_path(candidate_prompt_dir),
            "optimizer_result_dir": _display_path(candidate.optimizer_dir),
            "cost": {
                "estimated_usd": total_cost_usd,
                "optimizer_usd": optimizer_cost_usd,
                "evaluation_usd": evaluation_cost_usd,
                "total_usd": total_cost_usd,
                "max_budget_usd": config.get("gate", {}).get("max_cost_usd"),
            },
            "token_usage": candidate.token_usage or {"prompt": 0, "completion": 0, "total": 0},
            "source_prompt_updated": False,
        },
    }
    validate_report(report)
    _write_json(output_dir / "optimization_report.json", report)
    (output_dir / "optimization_report.md").write_text(build_markdown(report), encoding="utf-8")
    if args.update_sample_outputs:
        copy_sample_outputs(output_dir)
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["fake", "trace", "optimizer"],
        default="fake",
        help="fake and trace run without API keys; optimizer delegates candidate search to AgentOptimizer.",
    )
    parser.add_argument(
        "--scenario",
        choices=["overfit", "accepted", "cost_exceeded"],
        default="overfit",
        help="Deterministic fake/trace scenario. Ignored in optimizer mode.",
    )
    parser.add_argument("--output-dir", default=None, help="Optional output directory.")
    parser.add_argument(
        "--update-sample-outputs",
        action="store_true",
        help="Copy the generated report into sample_outputs/.",
    )
    parser.add_argument(
        "--ci-exit-code",
        action="store_true",
        help="Exit 0 when the gate accepts and 1 when it rejects. Default demo runs always exit 0.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = asyncio.run(run_pipeline(args))
    print(f"optimization report written to: {output_dir}")
    if args.ci_exit_code:
        report = _read_json(output_dir / "optimization_report.json")
        raise SystemExit(0 if report["gate"]["decision"] == "accepted" else 1)


if __name__ == "__main__":
    main()
