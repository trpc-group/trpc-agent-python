# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluation + optimization loop example.

The default mode is deterministic and offline. Fixtures provide only agent
outputs; AgentEvaluator remains the source of scores, pass/fail status, metric
details, and actual conversations. Online mode is explicit and gated by model
environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import difflib
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from trpc_agent_sdk.log import logger

TRAIN_PATH = HERE / "train.evalset.json"
OPTIMIZER_DEV_PATH = HERE / "optimizer_dev.evalset.json"
VAL_PATH = HERE / "val.evalset.json"
FIXTURE_PATH = HERE / "fixtures" / "fake_outputs.json"
TRACE_FIXTURE_PATH = HERE / "fixtures" / "trace_outputs.json"
OPTIMIZER_CONFIG_PATH = HERE / "optimizer.json"
REPORT_SCHEMA_PATH = HERE / "optimization_report.schema.json"
PROMPT_DIR = HERE / "agent" / "prompts"
SYSTEM_PROMPT_PATH = PROMPT_DIR / "system.md"
ROUTER_PROMPT_PATH = PROMPT_DIR / "router.md"
PROMPT_TARGET_NAMES = ("system_prompt", "router_prompt")
DEFAULT_RUNS_DIR = HERE / "runs"
DEFAULT_SEED = 7
DEFAULT_MAX_SECONDS = 180.0
PRIMARY_METRIC = "route_tool_args_score"
OFFLINE_RUBRIC_METRIC = "llm_rubric_response"
ONLINE_ENV_VARS = (
    "TRPC_AGENT_API_KEY",
    "TRPC_AGENT_BASE_URL",
    "TRPC_AGENT_MODEL_NAME",
)

TAXONOMY = (
    "final_response_mismatch",
    "tool_call_error",
    "parameter_error",
    "rubric_failed",
    "knowledge_gap",
    "format_error",
    "runtime_error",
    "metric_failed",
)

OFFLINE_METRICS_CONFIG = {
    "metrics": [
        {
            "metric_name": PRIMARY_METRIC,
            "threshold": 1.0,
            "criterion": {"final_response": {"json": {"match": "exact"}}},
        },
        {
            "metric_name": OFFLINE_RUBRIC_METRIC,
            "threshold": 1.0,
            "criterion": {"offline_rubric": {"checks": ["valid_json_object", "route_present", "tool_object_present"]}},
        },
    ],
    "num_runs": 1,
}

DEFAULT_GATE_CONFIG = {
    "min_validation_delta": 0.0,
    "allow_new_hard_fails": False,
    "allow_critical_regression": False,
    "max_cost_usd": None,
    "max_duration_seconds": DEFAULT_MAX_SECONDS,
    "required_metrics": [PRIMARY_METRIC],
}
GATE_CONFIG_KEYS = frozenset(DEFAULT_GATE_CONFIG)
GATE_RUNTIME_KEYS = GATE_CONFIG_KEYS | {"required_metrics_source"}

SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?$")
TOKEN_USAGE_KEYS = ("prompt", "completion", "total")
WINDOWS_RESERVED_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
INVALID_JSON_EVIDENCE = object()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def validate_report_schema(report: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator
    from jsonschema import ValidationError

    def reject_nonfinite_numbers(value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError("report contains a non-finite number")
        if isinstance(value, dict):
            for item in value.values():
                reject_nonfinite_numbers(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                reject_nonfinite_numbers(item)

    reject_nonfinite_numbers(report)
    schema = load_json(REPORT_SCHEMA_PATH)
    Draft202012Validator(schema).validate(report)

    def reject(message: str) -> None:
        raise ValidationError(message)

    if _is_windows_reserved_name(report.get("run_id")):
        reject("run_id uses a Windows reserved device basename")

    baseline = report["baseline"]
    if baseline["validation"] != baseline["final_validation"]:
        reject("baseline validation must equal final_validation")

    def validate_evaluation_summary(summary: dict[str, Any], label: str) -> None:
        case_results = summary["case_results"]
        expected_score = round(sum(case["score"] for case in case_results) / len(case_results), 6)
        if round(summary["score"], 6) != expected_score:
            reject(f"{label} summary score does not match case-derived score")
        expected_pass_rate = round(
            sum(1 for case in case_results if case["passed"]) / len(case_results),
            6,
        )
        if round(summary["pass_rate"], 6) != expected_pass_rate:
            reject(f"{label} summary pass_rate does not match case results")
        expected_failed_ids = [case["case_id"] for case in case_results if not case["passed"]]
        if summary["failed_case_ids"] != expected_failed_ids:
            reject(f"{label} summary failed_case_ids do not match case results")
        if summary["source"] != "AgentEvaluator":
            reject(f"{label} summary source must be AgentEvaluator")
        numeric_case_metric_names: set[str] = set()
        explicit_no_run_case_ids: set[str] = set()
        for case in case_results:
            case_metrics = case["metrics"]
            if not case_metrics:
                if not case["passed"] and case["key_trace"]["error_message"]:
                    if case["score"] != 0.0:
                        reject(f"{label} explicit no-run case score must be zero")
                    explicit_no_run_case_ids.add(case["case_id"])
                    continue
                reject(f"{label} case metric evidence is empty without an explicit failed run")
            for metric_name, metric in case_metrics.items():
                metric_score = _finite_float(metric["score"])
                metric_threshold = _finite_float(metric["threshold"])
                if metric_threshold is None:
                    reject(f"{label} case metric {metric_name} has an invalid threshold")
                if metric_score is None:
                    if metric["passed"] or metric["status"].lower() == "passed":
                        reject(f"{label} case metric {metric_name} has contradictory missing-score evidence")
                    continue
                numeric_case_metric_names.add(metric_name)
                expected_passed = metric_score >= metric_threshold
                expected_status = "passed" if expected_passed else "failed"
                if metric["passed"] is not expected_passed or metric["status"].lower() != expected_status:
                    reject(f"{label} case metric {metric_name} has contradictory score evidence")
            if case["passed"] and any(metric["passed"] is not True for metric in case_metrics.values()):
                reject(f"{label} passed case contains failed metric evidence")
            primary_metric = case_metrics.get(PRIMARY_METRIC)
            primary_score = _finite_float(primary_metric.get("score")) if isinstance(primary_metric, Mapping) else None
            if primary_score is not None:
                expected_case_score = round(primary_score, 6)
            else:
                fallback_scores = [
                    metric_score
                    for metric in case_metrics.values()
                    for metric_score in [_finite_float(metric.get("score"))]
                    if metric_score is not None
                ]
                expected_case_score = (
                    round(sum(fallback_scores) / len(fallback_scores), 6)
                    if fallback_scores
                    else (1.0 if case["passed"] else 0.0)
                )
            if round(case["score"], 6) != expected_case_score:
                reject(f"{label} case score does not match metric-derived score")
        if set(summary["metrics"]) != numeric_case_metric_names:
            reject(f"{label} metric coverage does not match numeric case metric evidence")
        for metric_name, metric in summary["metrics"].items():
            metric_score = _finite_float(metric["score"])
            metric_threshold = _finite_float(metric["threshold"])
            if metric_score is None or metric_threshold is None:
                reject(f"{label} summary metric {metric_name} must have numeric score and threshold")
            expected_passed = metric_score >= metric_threshold
            expected_status = "passed" if expected_passed else "failed"
            if metric["passed"] is not expected_passed or metric["status"].lower() != expected_status:
                reject(f"{label} summary metric {metric_name} has contradictory score evidence")
            case_metric_scores: list[float] = []
            for case in case_results:
                case_metric = case["metrics"].get(metric_name)
                if not isinstance(case_metric, Mapping):
                    if case["case_id"] in explicit_no_run_case_ids:
                        continue
                    reject(f"{label} metric coverage is missing {metric_name} for an executed case")
                case_metric_score = _finite_float(case_metric.get("score"))
                if case_metric_score is None:
                    continue
                case_metric_threshold = _finite_float(case_metric.get("threshold"))
                if case_metric_threshold != metric_threshold:
                    reject(f"{label} aggregate metric {metric_name} threshold does not match case evidence")
                case_metric_scores.append(case_metric_score)
            if not case_metric_scores:
                reject(f"{label} aggregate metric {metric_name} has no numeric case evidence")
            expected_metric_score = round(sum(case_metric_scores) / len(case_metric_scores), 6)
            if round(metric_score, 6) != expected_metric_score:
                reject(f"{label} aggregate metric {metric_name} score does not match case evidence")
        for case in case_results:
            trace = case["key_trace"]
            if trace["actual_final_response"] != case["actual_text"]:
                reject(f"{label} case {case['case_id']} key trace actual response does not match")
            if trace["expected_final_response"] != case["expected_text"]:
                reject(f"{label} case {case['case_id']} key trace expected response does not match")
            if case["passed"]:
                if case["root_cause"] or case["reasons"]:
                    reject(f"{label} passed case {case['case_id']} must not carry failure attribution")
            elif case["root_cause"] not in TAXONOMY or not case["reasons"]:
                reject(f"{label} failed case {case['case_id']} requires an explainable root cause")

    def reject_duplicate_cases(summary: dict[str, Any], label: str) -> None:
        case_ids = [case["case_id"] for case in summary["case_results"]]
        duplicates = sorted(case_id for case_id, count in Counter(case_ids).items() if count > 1)
        if duplicates:
            reject(f"{label} contains duplicate case_id values: {', '.join(duplicates)}")

    def validate_prompt_artifacts(artifacts: list[dict[str, Any]], label: str) -> None:
        names = [artifact["name"] for artifact in artifacts]
        if len(names) != len(set(names)):
            reject(f"{label} prompt artifact names must be unique")
        for artifact in artifacts:
            content = artifact["content"]
            if artifact["sha256"] != sha256_text(content):
                reject(f"{label} prompt artifact hash does not match embedded content")
            if artifact["source_written"]:
                reject(f"{label} prompt audit must not claim source writes")
            candidate_path = Path(artifact["candidate_path"])
            if not candidate_path.is_absolute():
                candidate_path = REPO_ROOT / candidate_path
            if candidate_path.is_file() and candidate_path.read_text(encoding="utf-8") != content:
                reject(f"{label} prompt artifact content does not match the referenced file")

    for summary_name in ("train", "optimizer_dev", "validation", "final_validation"):
        reject_duplicate_cases(baseline[summary_name], f"baseline.{summary_name}")
        validate_evaluation_summary(baseline[summary_name], f"baseline.{summary_name}")
    validate_prompt_artifacts(baseline["prompt_artifacts"], "baseline")
    baseline_prompts_by_name = {artifact["name"]: artifact for artifact in baseline["prompt_artifacts"]}

    if report["failure_attribution"] != attribution_for(baseline["validation"]):
        reject("top-level failure attribution does not match baseline validation failures")

    config_snapshot = report["config_snapshot"]
    if config_snapshot["mode"] != report["mode"] or config_snapshot["seed"] != report["seed"]:
        reject("config snapshot mode and seed must match the report")
    recorded_gate_config = config_snapshot.get("gate")
    if not isinstance(recorded_gate_config, Mapping):
        reject("config_snapshot.gate must be an object")
    evaluation_snapshot = config_snapshot["evaluation"]
    try:
        renormalized_evaluation = normalized_evaluation_config(evaluation_snapshot)
    except ValueError as error:
        reject(f"config snapshot evaluation is invalid: {error}")
    if renormalized_evaluation != evaluation_snapshot:
        reject("config snapshot evaluation must be normalized and credential-free")
    if config_snapshot["evaluation_sha256"] != sha256_json(evaluation_snapshot):
        reject("evaluation config hash does not match the normalized snapshot")
    expected_judge_multiplier = judge_calls_per_agent_call(evaluation_snapshot)
    config_paths = config_snapshot["paths"]
    expected_prompt_names = set(PROMPT_TARGET_NAMES)
    prompt_targets = config_snapshot["prompt_targets"]
    if set(prompt_targets) != expected_prompt_names:
        reject("prompt target manifest must contain exactly the registered target names")
    if set(baseline_prompts_by_name) != expected_prompt_names:
        reject("baseline prompt artifacts must match the registered target names")
    for name in PROMPT_TARGET_NAMES:
        target = prompt_targets[name]
        baseline_artifact = baseline_prompts_by_name[name]
        expected_source_path = config_paths[name]
        if target["source_path"] != expected_source_path:
            reject(f"prompt target source path does not match config paths: {name}")
        if report["artifacts"].get(name) != expected_source_path:
            reject(f"prompt source path does not match report artifacts: {name}")
        if baseline_artifact["source_path"] != expected_source_path:
            reject(f"baseline prompt source path does not match target manifest: {name}")
        if target["sha256"] != baseline_artifact["sha256"]:
            reject(f"prompt target hash does not match baseline content: {name}")
        if baseline_artifact["diff"] != prompt_diff(baseline_artifact["content"], baseline_artifact["content"], name):
            reject(f"baseline prompt diff does not match embedded baseline content: {name}")
        source_path = Path(expected_source_path)
        if not source_path.is_absolute():
            source_path = REPO_ROOT / source_path
        if not source_path.is_file():
            reject(f"referenced prompt source artifact must exist: {name}")
        source_text = source_path.read_text(encoding="utf-8")
        if sha256_text(source_text) != target["sha256"] or source_text != baseline_artifact["content"]:
            reject(f"prompt target manifest does not match the referenced source artifact: {name}")
    expected_optimizer_config_path = config_paths["optimizer_config"]
    if report["artifacts"].get("optimizer_config") != expected_optimizer_config_path:
        reject("optimizer config path does not match report artifacts")
    expected_optimizer_config_hash = config_snapshot["optimizer_config_sha256"]
    optimizer_config_artifact = Path(expected_optimizer_config_path)
    if not optimizer_config_artifact.is_absolute():
        optimizer_config_artifact = REPO_ROOT / optimizer_config_artifact
    if not optimizer_config_artifact.is_file():
        reject("referenced optimizer config artifact must exist")
    if sha256_json_file(optimizer_config_artifact) != expected_optimizer_config_hash:
        reject("optimizer config hash does not match the referenced config artifact")
    if report["mode"] == "online":
        runtime_evaluation = normalized_evaluation_config(
            validated_optimizer_evaluate_config(optimizer_config_artifact)
        )
        if runtime_evaluation != evaluation_snapshot:
            reject("evaluation snapshot does not match the runtime optimizer config")
    evaluation_metrics_path = Path(config_paths["evaluation_metrics"])
    if report["artifacts"].get("eval_metrics") != config_paths["evaluation_metrics"]:
        reject("evaluation metrics path does not match report artifacts")
    if not evaluation_metrics_path.is_absolute():
        evaluation_metrics_path = REPO_ROOT / evaluation_metrics_path
    if not evaluation_metrics_path.is_file():
        reject("referenced evaluation metrics artifact must exist")
    if sha256_json_file(evaluation_metrics_path) != config_snapshot["evaluation_metrics_sha256"]:
        reject("evaluation metrics hash does not match the referenced artifact")
    if normalized_evaluation_config(load_json(evaluation_metrics_path)) != evaluation_snapshot:
        reject("evaluation snapshot does not match the evaluation metrics artifact")

    manifest_path_keys = {
        "train": "train_evalset",
        "optimizer_dev": "optimizer_dev_evalset",
        "final_validation": "final_validation_evalset",
    }
    evalset_manifests = config_snapshot["evalsets"]
    for role, path_key in manifest_path_keys.items():
        manifest = evalset_manifests[role]
        if manifest["path"] != config_paths[path_key]:
            reject(f"{role} evalset manifest path does not match config paths")
        artifact_key = path_key
        if report["artifacts"].get(artifact_key) != manifest["path"]:
            reject(f"{role} evalset manifest path does not match report artifacts")
        evalset_path = Path(manifest["path"])
        if not evalset_path.is_absolute():
            evalset_path = REPO_ROOT / evalset_path
        if not evalset_path.is_file():
            reject(f"referenced {role} evalset artifact must exist")
        observed_manifest = build_evalset_manifest(evalset_path)
        for evidence_key in ("sha256", "case_count", "turn_count"):
            if manifest[evidence_key] != observed_manifest[evidence_key]:
                reject(f"{role} evalset manifest {evidence_key} does not match the referenced artifact")
    environment = report["environment_snapshot"]
    if environment["seed"] != report["seed"]:
        reject("environment snapshot seed must match the report")
    if environment["config_path"] != expected_optimizer_config_path:
        reject("environment config path must match the config snapshot")

    round_ids = [round_record["round"] for round_record in report["optimization_rounds"]]
    if len(round_ids) != len(set(round_ids)):
        reject("optimization round identifiers must be unique")
    for round_record in report["optimization_rounds"]:
        prompt_keys = set(round_record["prompt_paths"])
        if not prompt_keys.issubset(expected_prompt_names):
            reject("optimization round contains an unknown prompt target")
        if prompt_keys != set(round_record["prompt_sha256"]) or prompt_keys != set(round_record["prompt_contents"]):
            reject("optimization round prompt path, hash, and content keys must match")
        for name in prompt_keys:
            content = round_record["prompt_contents"][name]
            if round_record["prompt_sha256"][name] != sha256_text(content):
                reject("optimization round prompt hash does not match embedded content")
            prompt_path = Path(round_record["prompt_paths"][name])
            if not prompt_path.is_absolute():
                prompt_path = REPO_ROOT / prompt_path
            if prompt_path.is_file() and prompt_path.read_text(encoding="utf-8") != content:
                reject("optimization round prompt content does not match the referenced file")
        optimized_field_names = set(round_record["optimized_field_names"])
        if (round_record["accepted"] or optimized_field_names) and not prompt_keys:
            reject("accepted or optimizing rounds must contain prompt evidence")
        if not optimized_field_names.issubset(prompt_keys):
            reject("optimization round fields must be present in prompt evidence")
    cost = report["cost"]
    if report["mode"] == "online":
        online_duration = report.get("online_duration")
        if not isinstance(online_duration, Mapping):
            reject("online report must include online_duration evidence")
        expected_gate_elapsed = round(
            online_duration["optimization_seconds"]
            + online_duration["baseline_revalidation_seconds"]
            + online_duration["candidate_revalidation_seconds"],
            6,
        )
        if not math.isclose(
            online_duration["gate_elapsed_seconds"],
            expected_gate_elapsed,
            rel_tol=0.0,
            abs_tol=0.000002,
        ):
            reject("online gate elapsed duration does not match recorded phases")
        if report["duration_seconds"] + 0.000002 < online_duration["gate_elapsed_seconds"]:
            reject("total duration must cover online gate elapsed duration")

    candidates_by_id: dict[str, dict[str, Any]] = {}
    for candidate in report["candidates"]:
        candidate_id = candidate["id"]
        if candidate_id in candidates_by_id:
            reject(f"report contains duplicate candidate id: {candidate_id}")
        if _is_windows_reserved_name(candidate_id):
            reject("candidate id uses a Windows reserved device basename")
        if candidate["gate"]["candidate_id"] != candidate_id:
            reject("candidate gate candidate_id must equal candidate id")
        if candidate["validation"] != candidate["final_validation"]:
            reject("candidate validation must equal final_validation")
        for summary_name in ("train", "optimizer_dev", "validation", "final_validation"):
            reject_duplicate_cases(candidate[summary_name], f"candidate {candidate_id}.{summary_name}")
            validate_evaluation_summary(candidate[summary_name], f"candidate {candidate_id}.{summary_name}")
        for summary_name in ("train", "optimizer_dev"):
            baseline_case_ids = {case["case_id"] for case in baseline[summary_name]["case_results"]}
            candidate_case_ids = {case["case_id"] for case in candidate[summary_name]["case_results"]}
            if candidate_case_ids != baseline_case_ids:
                reject(f"candidate {candidate_id}.{summary_name} case set must match baseline")
        expected_delta = {
            "train_score": _score_delta(candidate["train"]["score"], baseline["train"]["score"]),
            "optimizer_dev_score": _score_delta(
                candidate["optimizer_dev"]["score"],
                baseline["optimizer_dev"]["score"],
            ),
            "validation_score": _score_delta(
                candidate["validation"]["score"],
                baseline["validation"]["score"],
            ),
        }
        if candidate["delta"] != expected_delta:
            reject(f"candidate delta does not match recomputed six-decimal deltas: {candidate_id}")
        if candidate["gate"]["validation_delta"] != expected_delta["validation_score"]:
            reject(f"candidate gate validation_delta does not match candidate delta: {candidate_id}")
        expected_case_deltas = build_case_deltas(baseline["validation"], candidate["validation"])
        if candidate["case_deltas"] != expected_case_deltas:
            reject(f"candidate case_deltas do not match recomputed validation case deltas: {candidate_id}")
        if candidate["failure_attribution"] != attribution_for(candidate["validation"]):
            reject(f"candidate failure attribution does not match validation failures: {candidate_id}")

        candidate_audit = candidate["audit"]
        if candidate_audit["seed"] != report["seed"]:
            reject(f"candidate audit seed does not match report seed: {candidate_id}")
        if candidate_audit["config_path"] != expected_optimizer_config_path:
            reject(f"candidate audit config path does not match config snapshot: {candidate_id}")
        if candidate_audit["config_sha256"] != expected_optimizer_config_hash:
            reject(f"candidate audit config hash does not match config snapshot: {candidate_id}")
        validate_prompt_artifacts(candidate["prompt_artifacts"], f"candidate {candidate_id}")
        candidate_prompts_by_name = {artifact["name"]: artifact for artifact in candidate["prompt_artifacts"]}
        if set(candidate_prompts_by_name) != expected_prompt_names:
            reject(f"candidate prompt artifact names must match baseline targets: {candidate_id}")
        for name in PROMPT_TARGET_NAMES:
            candidate_artifact = candidate_prompts_by_name[name]
            baseline_artifact = baseline_prompts_by_name[name]
            if candidate_artifact["source_path"] != prompt_targets[name]["source_path"]:
                reject(f"candidate prompt source path must match target manifest: {candidate_id}/{name}")
            expected_diff = prompt_diff(
                baseline_artifact["content"],
                candidate_artifact["content"],
                name,
            )
            if candidate_artifact["diff"] != expected_diff:
                reject(f"candidate prompt diff does not match embedded baseline: {candidate_id}/{name}")
        pipeline_cost = cost["estimated_total"]
        audit_cost = candidate_audit["cost"]
        if audit_cost["known"] is not (pipeline_cost is not None) or audit_cost["estimated"] != pipeline_cost:
            reject(f"candidate audit cost does not match pipeline cost evidence: {candidate_id}")
        if candidate_audit["duration_seconds"] > report["duration_seconds"]:
            reject(f"candidate audit duration exceeds total report duration: {candidate_id}")

        gate_duration = candidate_audit["duration_seconds"]
        if report["mode"] == "online":
            gate_duration = online_duration.get("gate_elapsed_seconds")
            if candidate_audit["duration_seconds"] != online_duration["candidate_revalidation_seconds"]:
                reject("candidate audit duration does not match online candidate revalidation phase")
        expected_gate = apply_gate(
            candidate_id=candidate_id,
            baseline_val=baseline["validation"],
            candidate_val=candidate["validation"],
            gate_config=recorded_gate_config,
            duration_seconds=gate_duration,
            cost_usd=audit_cost["estimated"] if audit_cost["known"] else None,
        )
        if report["mode"] == "online":
            online_result = report.get("online_result")
            if not isinstance(online_result, Mapping):
                reject("online report must include online_result evidence")
            if online_result.get("status") != "SUCCEEDED":
                expected_gate["accepted"] = False
                expected_gate["reasons"].append(f"native optimizer status was {online_result.get('status')}")
                if online_result.get("error_message"):
                    expected_gate["reasons"].append(online_result["error_message"])
            if not cost["optimizer"]["usage_evidence_valid"]:
                expected_gate["accepted"] = False
                expected_gate["reasons"].append("optimizer usage evidence was malformed and cannot be audited")
        if candidate["gate"] != expected_gate:
            reject(f"candidate gate evidence does not match recomputed gate evidence: {candidate_id}")
        if candidate["gate"]["accepted"]:
            if candidate["gate"]["validation_delta"] <= 0:
                reject("accepted candidate validation delta must be strictly positive")
        candidates_by_id[candidate_id] = candidate

    expected_winner = pick_winner(report["candidates"])
    if expected_winner is not None:
        expected_decision = {
            "accepted": True,
            "winner": expected_winner["id"],
            "reasons": expected_winner["gate"]["reasons"],
        }
        expected_top_delta = expected_winner["delta"]
    else:
        rejection_reasons = ["no candidate passed all gates"]
        for candidate in report["candidates"]:
            rejection_reasons.extend(f"{candidate['id']}: {reason}" for reason in candidate["gate"]["reasons"])
        expected_decision = {
            "accepted": False,
            "winner": None,
            "reasons": rejection_reasons,
        }
        expected_top_delta = {
            "validation_score": 0.0,
            "optimizer_dev_score": 0.0,
            "train_score": 0.0,
        }
    if report["gate_decision"] != expected_decision:
        reject("top-level gate decision does not match recomputed candidate decisions")
    if report["delta"] != expected_top_delta:
        reject("top-level delta does not match recomputed winner delta")

    optimizer_cost = cost["optimizer"]
    final_cost = cost["final_revalidation"]
    if report["mode"] == "online":
        if optimizer_cost["judge_calls_per_candidate_evaluation"] != expected_judge_multiplier:
            reject("optimizer judge multiplier does not match the recorded evaluation config")
        if final_cost["judge_calls_per_agent_call"] != expected_judge_multiplier:
            reject("final judge multiplier does not match the recorded evaluation config")
    else:
        offline_call_evidence = (
            optimizer_cost["model_calls"],
            optimizer_cost["candidate_evaluation_agent_calls"],
            optimizer_cost["reflection_lm_calls"],
            optimizer_cost["judge_calls_per_candidate_evaluation"],
            optimizer_cost["judge_model_calls"],
            optimizer_cost["native_judge_model_calls"],
            optimizer_cost["derived_judge_model_calls"],
            final_cost["agent_calls_per_run"],
            final_cost["agent_calls"],
            final_cost["judge_calls_per_agent_call"],
            final_cost["judge_model_calls"],
            final_cost["model_calls"],
            cost["model_calls"],
        )
        if any(offline_call_evidence):
            reject("offline reports must record zero optimizer and provider model calls")
    native_judge_calls = optimizer_cost["native_judge_model_calls"]
    derived_judge_calls = optimizer_cost["derived_judge_model_calls"]
    expected_derived_judge_calls = (
        optimizer_cost["candidate_evaluation_agent_calls"] * optimizer_cost["judge_calls_per_candidate_evaluation"]
    )
    if derived_judge_calls != expected_derived_judge_calls:
        reject("optimizer derived judge calls do not match candidate calls and recorded multiplier")
    expected_judge_calls = max(native_judge_calls, derived_judge_calls)
    if optimizer_cost["judge_model_calls"] != expected_judge_calls:
        reject("optimizer judge_model_calls must reconcile native and derived counts without double counting")
    if native_judge_calls and derived_judge_calls:
        expected_judge_source = (
            "native_and_derived_agree"
            if native_judge_calls == derived_judge_calls
            else "reconciled_native_and_derived_max"
        )
    elif native_judge_calls:
        expected_judge_source = "native_optimizer_counter"
    elif derived_judge_calls:
        expected_judge_source = "derived_from_candidate_calls_and_llm_metrics"
    else:
        expected_judge_source = "none"
    if optimizer_cost["judge_model_call_source"] != expected_judge_source:
        reject("optimizer judge_model_call_source does not match reconciled judge evidence")
    expected_optimizer_calls = (
        optimizer_cost["candidate_evaluation_agent_calls"]
        + optimizer_cost["reflection_lm_calls"]
        + optimizer_cost["judge_model_calls"]
    )
    if optimizer_cost["model_calls"] != expected_optimizer_calls:
        reject("optimizer model_calls must equal candidate, reflection, and judge calls")
    if report["mode"] == "online":
        expected_agent_calls_per_run = (1 + len(report["candidates"])) * sum(
            manifest["turn_count"] for manifest in evalset_manifests.values()
        )
        if final_cost["agent_calls_per_run"] != expected_agent_calls_per_run:
            reject("final revalidation per-run calls do not match authenticated evalset turns")
    expected_final_agent_calls = final_cost["agent_calls_per_run"] * evaluation_snapshot["num_runs"]
    if final_cost["agent_calls"] != expected_final_agent_calls:
        reject("final revalidation agent calls do not match per-run calls and evaluation num_runs")
    expected_final_judge_calls = final_cost["agent_calls"] * final_cost["judge_calls_per_agent_call"]
    if final_cost["judge_model_calls"] != expected_final_judge_calls:
        reject("final revalidation judge calls do not match agent calls and recorded multiplier")
    if final_cost["model_calls"] != final_cost["agent_calls"] + final_cost["judge_model_calls"]:
        reject("final revalidation model_calls must equal agent plus judge calls")
    expected_model_calls = cost["optimizer"]["model_calls"] + cost["final_revalidation"]["model_calls"]
    if cost["model_calls"] != expected_model_calls:
        reject("top-level model_calls must equal optimizer plus final revalidation calls")

    reflection_usage = optimizer_cost["reflection_reported_usage"]
    if reflection_usage["token_usage_known"]:
        if reflection_usage["unknown_token_usage_reason"] is not None:
            reject("known optimizer reflection token usage must not carry an unknown reason")
    elif not reflection_usage["unknown_token_usage_reason"]:
        reject("unknown optimizer reflection token usage must carry a reason")
    for scope, label in (
        (optimizer_cost, "optimizer"),
        (final_cost, "final revalidation"),
        (cost, "top-level"),
    ):
        if scope["token_usage_known"]:
            if scope["token_usage"] is None or scope["unknown_token_usage_reason"] is not None:
                reject(f"{label} known token usage must provide counters without an unknown reason")
        elif scope["token_usage"] is not None or not scope["unknown_token_usage_reason"]:
            reject(f"{label} unknown token usage must be null with a reason")

    unknown_optimizer_calls = (
        optimizer_cost["candidate_evaluation_agent_calls"] > 0 or optimizer_cost["judge_model_calls"] > 0
    )
    if unknown_optimizer_calls or not optimizer_cost["usage_evidence_valid"]:
        if (
            optimizer_cost["estimated_cost"] is not None
            or optimizer_cost["token_usage"] is not None
            or optimizer_cost["token_usage_known"]
        ):
            reject("optimizer calls with unscoped usage must force unknown phase cost and tokens")
    else:
        if optimizer_cost["estimated_cost"] != reflection_usage["estimated_cost"]:
            reject("optimizer phase cost must match scoped reflection cost when fully known")
        if optimizer_cost["token_usage"] != reflection_usage["token_usage"]:
            reject("optimizer phase tokens must match scoped reflection tokens when fully known")
    if optimizer_cost["usage_evidence_valid"] and (
        reflection_usage["estimated_cost"] is None or not reflection_usage["token_usage_known"]
    ):
        reject("valid optimizer usage evidence requires known scoped reflection usage")
    if (
        optimizer_cost["usage_evidence_valid"]
        and optimizer_cost["reflection_lm_calls"] == 0
        and (reflection_usage["estimated_cost"] != 0 or reflection_usage["token_usage"]["total"] != 0)
    ):
        reject("zero reflection calls require zero scoped reflection cost and tokens")
    if optimizer_cost["candidate_evaluation_agent_calls"] > 0 and "candidate-evaluation" not in (
        optimizer_cost["unknown_token_usage_reason"] or ""
    ):
        reject("optimizer candidate-evaluation unknown token reason must name that scope")
    if optimizer_cost["candidate_evaluation_agent_calls"] > 0 and (
        "candidate-evaluation" not in (cost["unknown_cost_reason"] or "")
        or "candidate-evaluation" not in (cost["unknown_token_usage_reason"] or "")
    ):
        reject("top-level unknown usage reasons must preserve candidate-evaluation scope")
    if optimizer_cost["judge_model_calls"] > 0 and "judge" not in (optimizer_cost["unknown_token_usage_reason"] or ""):
        reject("optimizer judge unknown token reason must name that scope")
    if optimizer_cost["judge_model_calls"] > 0 and (
        "judge" not in (cost["unknown_cost_reason"] or "") or "judge" not in (cost["unknown_token_usage_reason"] or "")
    ):
        reject("top-level unknown usage reasons must preserve optimizer judge scope")

    if final_cost["model_calls"] == 0:
        if final_cost["estimated_cost"] != 0 or not final_cost["token_usage_known"]:
            reject("zero-call final revalidation must have known zero cost and tokens")
    elif final_cost["estimated_cost"] is not None or final_cost["token_usage_known"]:
        reject("final revalidation calls without usage counters must remain unknown")

    scoped_costs_known = optimizer_cost["estimated_cost"] is not None and final_cost["estimated_cost"] is not None
    if scoped_costs_known:
        if cost["estimated_total"] != optimizer_cost["estimated_cost"] + final_cost["estimated_cost"]:
            reject("top-level estimated cost must equal known scoped costs")
        if cost["cost_source"] == "unknown" or cost["unknown_cost_reason"] is not None:
            reject("known top-level cost must not carry unknown cost evidence")
    elif cost["estimated_total"] is not None or cost["cost_source"] != "unknown" or not cost["unknown_cost_reason"]:
        reject("unknown scoped cost must propagate to top-level cost fields")

    scoped_tokens_known = optimizer_cost["token_usage_known"] and final_cost["token_usage_known"]
    if scoped_tokens_known:
        expected_token_usage = {
            key: optimizer_cost["token_usage"][key] + final_cost["token_usage"][key] for key in TOKEN_USAGE_KEYS
        }
        if not cost["token_usage_known"] or cost["token_usage"] != expected_token_usage:
            reject("top-level token usage must equal known scoped token usage")
    elif cost["token_usage_known"] or cost["token_usage"] is not None or not cost["unknown_token_usage_reason"]:
        reject("unknown scoped token usage must propagate to top-level token fields")

    def validate_token_totals(value: Any) -> None:
        if isinstance(value, dict):
            if set(value) == set(TOKEN_USAGE_KEYS) and all(
                isinstance(value[key], int) and not isinstance(value[key], bool) for key in TOKEN_USAGE_KEYS
            ):
                if value["total"] != value["prompt"] + value["completion"]:
                    reject("token usage total must equal prompt plus completion")
            for nested in value.values():
                validate_token_totals(nested)
        elif isinstance(value, list):
            for nested in value:
                validate_token_totals(nested)

    validate_token_totals(report)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return sha256_text(canonical)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_json_file(path: Path) -> str:
    return sha256_json(load_json(path))


def _redact_evaluation_config(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in {
                "apikey",
                "authorization",
                "proxyauthorization",
                "baseurl",
                "headers",
                "cookies",
                "accesstoken",
                "sessiontoken",
            }:
                continue
            redacted[str(key)] = _redact_evaluation_config(item)
        return redacted
    if isinstance(value, list):
        return [_redact_evaluation_config(item) for item in value]
    return value


def normalized_evaluation_config(metrics_config: Mapping[str, Any]) -> dict[str, Any]:
    from trpc_agent_sdk.evaluation._eval_config import EvalConfig

    try:
        eval_config = EvalConfig.model_validate(dict(metrics_config))
    except Exception as error:
        raise ValueError(f"invalid evaluation metrics config: {error}") from error
    metrics = []
    for metric in eval_config.get_eval_metrics():
        metrics.append(
            {
                "metric_name": metric.metric_name,
                "threshold": float(metric.threshold),
                "criterion": _redact_evaluation_config(copy.deepcopy(metric.criterion)),
            }
        )
    snapshot: dict[str, Any] = {
        "metrics": metrics,
        "num_runs": eval_config.num_runs,
    }
    if eval_config.user_simulator_config is not None:
        user_simulator = eval_config.user_simulator_config
        if hasattr(user_simulator, "model_dump"):
            user_simulator = user_simulator.model_dump(mode="json")
        snapshot["user_simulator_config"] = _redact_evaluation_config(user_simulator)
    return snapshot


def build_evalset_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    cases = payload.get("eval_cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"evalset manifest requires non-empty eval_cases: {path}")
    turn_count = 0
    for case in cases:
        conversation = case.get("conversation") if isinstance(case, dict) else None
        if not isinstance(conversation, list) or not conversation:
            raise ValueError(f"evalset manifest requires non-empty conversations: {path}")
        turn_count += len(conversation)
    return {
        "path": str(path),
        "sha256": sha256_json_file(path),
        "case_count": len(cases),
        "turn_count": turn_count,
    }


def build_evalset_manifests(
    train_evalset: Path,
    optimizer_dev_evalset: Path,
    val_evalset: Path,
) -> dict[str, dict[str, Any]]:
    return {
        "train": build_evalset_manifest(train_evalset),
        "optimizer_dev": build_evalset_manifest(optimizer_dev_evalset),
        "final_validation": build_evalset_manifest(val_evalset),
    }


def build_candidate_audit(
    *,
    seed: int,
    duration_seconds: float,
    cost_usd: float | None,
    optimizer_config: Path,
) -> dict[str, Any]:
    return {
        "seed": seed,
        "duration_seconds": round(duration_seconds, 6),
        "cost": {
            "currency": "USD",
            "estimated": cost_usd,
            "known": cost_usd is not None,
        },
        "config_path": str(optimizer_config),
        "config_sha256": sha256_json_file(optimizer_config),
    }


def _normalized_round_number(value: Any, *, nonnegative: bool = False) -> tuple[int | float, bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, True
    if not math.isfinite(value) or (nonnegative and value < 0):
        return 0.0, True
    return value, False


def _normalized_round_count(value: Any) -> tuple[int, bool]:
    normalized, invalid = _normalized_round_number(value, nonnegative=True)
    if invalid:
        return 0, True
    if isinstance(normalized, int):
        return normalized, False
    if normalized.is_integer():
        return int(normalized), False
    return 0, True


def _normalized_token_usage(value: Any) -> tuple[dict[str, int], bool]:
    empty = {name: 0 for name in TOKEN_USAGE_KEYS}
    if not isinstance(value, dict) or set(value) != set(TOKEN_USAGE_KEYS):
        return empty, True
    normalized: dict[str, int] = {}
    for name in TOKEN_USAGE_KEYS:
        count, invalid = _normalized_round_count(value[name])
        if invalid:
            return empty, True
        normalized[name] = count
    if normalized["total"] != normalized["prompt"] + normalized["completion"]:
        return empty, True
    return normalized, False


def _normalized_round_identifier(value: Any) -> tuple[int, bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0, True
    if isinstance(value, float) and not math.isfinite(value):
        return 0, True
    if value <= 0 or (isinstance(value, float) and not value.is_integer()):
        return 0, True
    return int(value), False


def _normalized_round_list(value: Any) -> tuple[list[Any], bool]:
    if not isinstance(value, (list, tuple)):
        return [], True
    normalized_values: list[str] = []
    invalid_member = False
    for member in value:
        if isinstance(member, str):
            normalized_values.append(member)
        else:
            invalid_member = True
    return normalized_values, invalid_member


def _prompt_sort_key(item: tuple[Any, Any]) -> tuple[str, str]:
    name = item[0]
    return type(name).__name__, repr(name)


def write_optimizer_round_artifacts(
    *,
    run_dir: Path,
    rounds: list[Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    reserved_round_ids = {
        round_id
        for round_record in rounds
        for round_id, invalid in [_normalized_round_identifier(getattr(round_record, "round", None))]
        if not invalid
    }
    used_round_ids: set[int] = set()
    next_fallback_round_id = 1
    for round_record in rounds:
        round_id, invalid_round_id = _normalized_round_identifier(getattr(round_record, "round", None))
        duplicate_round_id = not invalid_round_id and round_id in used_round_ids
        if invalid_round_id or duplicate_round_id:
            while next_fallback_round_id in reserved_round_ids or next_fallback_round_id in used_round_ids:
                next_fallback_round_id += 1
            round_id = next_fallback_round_id
            next_fallback_round_id += 1
        used_round_ids.add(round_id)
        round_component = f"optimizer_round_{round_id:03d}"
        round_dir = _resolved_run_descendant(
            run_dir,
            "prompts",
            round_component,
            label="optimizer round prompt directory",
        )
        round_dir.mkdir(parents=True, exist_ok=True)
        prompt_paths: dict[str, str] = {}
        prompt_hashes: dict[str, str] = {}
        prompt_contents: dict[str, str] = {}
        prompt_reasons: list[str] = []
        raw_candidate_prompts = getattr(round_record, "candidate_prompts", None)
        invalid_prompt_evidence = not isinstance(raw_candidate_prompts, dict)
        if invalid_prompt_evidence:
            prompt_reasons.append("candidate_prompts was not a mapping and was normalized to an empty mapping")
            raw_candidate_prompts = {}
        raw_prompt_items = sorted(raw_candidate_prompts.items(), key=_prompt_sort_key)
        for raw_name, raw_content in raw_prompt_items:
            if not isinstance(raw_name, str) or raw_name not in PROMPT_TARGET_NAMES:
                invalid_prompt_evidence = True
                if "unknown prompt target was dropped" not in prompt_reasons:
                    prompt_reasons.append("unknown prompt target was dropped")
                continue
            name = raw_name
            if isinstance(raw_content, str):
                content = raw_content
            else:
                content = ""
                invalid_prompt_evidence = True
                if "prompt content was normalized to an empty string" not in prompt_reasons:
                    prompt_reasons.append("prompt content was normalized to an empty string")
            prompt_path = _resolved_run_descendant(
                run_dir,
                "prompts",
                round_component,
                f"{name}.md",
                label="optimizer round prompt artifact",
            )
            prompt_path.write_text(content, encoding="utf-8")
            prompt_paths[name] = str(prompt_path)
            prompt_hashes[name] = sha256_text(content)
            prompt_contents[name] = content
        validation_pass_rate, invalid_validation_pass_rate = _normalized_round_number(
            getattr(round_record, "validation_pass_rate", None),
            nonnegative=True,
        )
        invalid_rate_bounds = invalid_validation_pass_rate or validation_pass_rate > 1
        if invalid_rate_bounds:
            validation_pass_rate = 0.0
        metric_breakdown: dict[str, int | float] = {}
        invalid_numeric_evidence = invalid_validation_pass_rate
        raw_metric_breakdown = getattr(round_record, "metric_breakdown", {})
        if not isinstance(raw_metric_breakdown, dict):
            raw_metric_breakdown = {}
            invalid_numeric_evidence = True
        invalid_mapping_key_evidence = False
        for name, value in raw_metric_breakdown.items():
            if not isinstance(name, str):
                invalid_mapping_key_evidence = True
                continue
            normalized, invalid = _normalized_round_number(value)
            metric_breakdown[name] = normalized
            invalid_numeric_evidence = invalid_numeric_evidence or invalid
        cost_usd, invalid_cost = _normalized_round_number(
            getattr(round_record, "round_llm_cost", None),
            nonnegative=True,
        )
        duration_seconds, invalid_duration = _normalized_round_number(
            getattr(round_record, "duration_seconds", None),
            nonnegative=True,
        )
        invalid_numeric_evidence = invalid_numeric_evidence or invalid_cost or invalid_duration
        raw_token_usage = getattr(round_record, "round_token_usage", {})
        token_usage, invalid_token_usage = _normalized_token_usage(raw_token_usage)
        invalid_numeric_evidence = invalid_numeric_evidence or invalid_token_usage
        invalid_collection_evidence = False
        optimized_field_names, invalid_optimized_field_names = _normalized_round_list(
            getattr(round_record, "optimized_field_names", None)
        )
        failed_case_ids, invalid_failed_case_ids = _normalized_round_list(
            getattr(round_record, "failed_case_ids", None)
        )
        invalid_collection_evidence = invalid_optimized_field_names or invalid_failed_case_ids
        reason_values: list[str] = []
        invalid_reason_evidence = False
        for field_name in ("acceptance_reason", "skip_reason", "error_message"):
            value = getattr(round_record, field_name, None)
            if value is None:
                continue
            if not isinstance(value, str):
                invalid_reason_evidence = True
                continue
            if value:
                reason_values.append(value)
                break
        decision_reason = sanitize_report_text(reason_values[0] if reason_values else "") or (
            "optimizer reported no reason"
        )
        accepted = getattr(round_record, "accepted", False) is True
        if (
            invalid_round_id
            or duplicate_round_id
            or invalid_numeric_evidence
            or invalid_rate_bounds
            or invalid_prompt_evidence
            or invalid_collection_evidence
            or invalid_reason_evidence
            or invalid_mapping_key_evidence
        ):
            accepted = False
            if invalid_numeric_evidence:
                decision_reason += "; invalid numeric round evidence was normalized and rejected"
            if invalid_token_usage:
                decision_reason += "; invalid token usage was normalized to prompt/completion/total zeros"
            if invalid_round_id:
                decision_reason += f"; invalid round identifier was normalized to {round_id} and rejected"
            if duplicate_round_id:
                decision_reason += f"; duplicate round identifier was normalized to {round_id} and rejected"
            if invalid_rate_bounds:
                decision_reason += "; validation_pass_rate was out of bounds and normalized to 0.0; round rejected"
            if invalid_collection_evidence:
                decision_reason += "; invalid round collections were normalized and rejected"
            if invalid_reason_evidence:
                decision_reason += "; invalid round reason fields were ignored and rejected"
            if invalid_mapping_key_evidence:
                decision_reason += "; invalid mapping keys were dropped and rejected"
            for prompt_reason in prompt_reasons:
                decision_reason += f"; {prompt_reason}; round rejected"
        missing_prompt_fields = [name for name in optimized_field_names if name not in prompt_paths]
        if missing_prompt_fields:
            accepted = False
            optimized_field_names = [name for name in optimized_field_names if name in prompt_paths]
            decision_reason += "; optimized fields without prompt evidence were dropped and rejected"
        if accepted and (not optimized_field_names or not prompt_paths):
            accepted = False
            decision_reason += "; accepted round lacked auditable optimized prompt evidence and was rejected"
        records.append(
            {
                "round": round_id,
                "optimized_field_names": optimized_field_names,
                "prompt_paths": prompt_paths,
                "prompt_sha256": prompt_hashes,
                "prompt_contents": prompt_contents,
                "validation_pass_rate": float(validation_pass_rate),
                "metric_breakdown": metric_breakdown,
                "accepted": accepted,
                "decision_reason": decision_reason,
                "failed_case_ids": failed_case_ids,
                "cost_usd": float(cost_usd),
                "token_usage": token_usage,
                "duration_seconds": float(duration_seconds),
            }
        )
    return records


def _git_output(*args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 - environment snapshot must not fail the run.
        return None
    return proc.stdout.strip()


def sdk_version() -> str | None:
    try:
        return importlib.metadata.version("trpc-agent-py")
    except importlib.metadata.PackageNotFoundError:
        return None


def base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    return parsed.hostname or None


def environment_snapshot(
    *,
    seed: int,
    command: str | None,
    config_path: str,
) -> dict[str, Any]:
    return {
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_dirty": bool(_git_output("status", "--porcelain")),
        "python_version": platform.python_version(),
        "sdk_version": sdk_version(),
        "model_name": os.getenv("TRPC_AGENT_MODEL_NAME"),
        "base_url_host": base_url_host(os.getenv("TRPC_AGENT_BASE_URL")),
        "seed": seed,
        "command": command or "programmatic",
        "config_path": config_path,
    }


def resolve_path(path: Path | None, default: Path) -> Path:
    return (path or default).expanduser().resolve()


def optimizer_metric_names(config_path: Path) -> list[str]:
    payload = load_json(config_path)
    evaluate = payload.get("evaluate") if isinstance(payload, dict) else None
    metrics = evaluate.get("metrics") if isinstance(evaluate, dict) else None
    if not isinstance(metrics, list):
        raise ValueError("optimizer evaluate.metrics must be an array")
    names: list[str] = []
    for position, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            raise ValueError(f"optimizer evaluate.metrics[{position}] must be an object")
        name = metric.get("metric_name") or metric.get("metricName")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"optimizer evaluate.metrics[{position}].metric_name must be a non-empty string")
        names.append(name)
    if len(set(names)) != len(names):
        raise ValueError("optimizer evaluate.metrics metric_name values must be unique")
    return names


def optimizer_required_metrics(config_path: Path) -> tuple[list[str], str]:
    payload = load_json(config_path)
    optimize = payload.get("optimize") if isinstance(payload, dict) else None
    stop = optimize.get("stop") if isinstance(optimize, dict) else None
    if not isinstance(stop, dict):
        raise ValueError("optimizer optimize.stop must be an object")
    required = stop.get("required_metrics")
    if required is None:
        return [], "optimizer_config"
    if required == "all":
        return optimizer_metric_names(config_path), "optimizer_config"
    if not isinstance(required, list):
        raise ValueError("optimizer required_metrics must be 'all', null, or an array of strings")
    if any(not isinstance(name, str) or not name.strip() for name in required):
        raise ValueError("optimizer required_metrics must contain only non-empty strings")
    if len(set(required)) != len(required):
        raise ValueError("optimizer required_metrics must contain unique strings")
    unknown = sorted(set(required) - set(optimizer_metric_names(config_path)))
    if unknown:
        raise ValueError("optimizer required_metrics references unknown metrics: " + ", ".join(unknown))
    return required, "optimizer_config"


def validated_optimizer_evaluate_config(config_path: Path) -> dict[str, Any]:
    from trpc_agent_sdk.evaluation import load_optimize_config

    try:
        load_optimize_config(str(config_path))
        optimizer_required_metrics(config_path)
    except Exception as error:
        raise ValueError(f"invalid optimizer config: {error}") from error
    payload = load_json(config_path)
    return payload["evaluate"]


def materialize_optimizer_config(
    *,
    run_dir: Path,
    source_config: Path,
    seed: int,
) -> Path:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("optimizer seed must be an integer")
    validated_optimizer_evaluate_config(source_config)
    payload = load_json(source_config)
    optimize = payload.get("optimize") if isinstance(payload, dict) else None
    algorithm = optimize.get("algorithm") if isinstance(optimize, dict) else None
    if not isinstance(algorithm, dict):
        raise ValueError("optimizer optimize.algorithm must be an object")
    runtime_payload = copy.deepcopy(payload)
    runtime_payload["optimize"]["algorithm"]["seed"] = seed
    runtime_path = _resolved_run_descendant(
        run_dir,
        "optimizer.json",
        label="runtime optimizer config artifact",
    )
    write_json(runtime_path, runtime_payload)
    validated_optimizer_evaluate_config(runtime_path)
    return runtime_path


def online_preflight() -> dict[str, bool]:
    return {name: bool(os.getenv(name)) for name in ONLINE_ENV_VARS}


def format_online_preflight(preflight: dict[str, bool]) -> str:
    parts = [f"{name}={'present' if preflight.get(name) else 'missing'}" for name in ONLINE_ENV_VARS]
    return "online preflight: " + " ".join(parts)


def require_online_preflight() -> dict[str, bool]:
    preflight = online_preflight()
    missing = [name for name, exists in preflight.items() if not exists]
    if missing:
        raise ValueError(
            format_online_preflight(preflight)
            + "; online mode requires environment variables: "
            + ", ".join(ONLINE_ENV_VARS)
            + f"; missing: {', '.join(missing)}"
        )
    return preflight


def final_text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        parts = content.get("parts") or []
    else:
        parts = getattr(content, "parts", None) or []
    return "\n".join(
        str((part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")) or "")
        for part in parts
        if not (part.get("thought", False) if isinstance(part, dict) else getattr(part, "thought", False))
    ).strip()


def parsed_json_evidence(content: Any) -> Any:
    visible_text = final_text_from_content(content)
    try:
        parsed = json.loads(visible_text)
        json.dumps(
            parsed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return INVALID_JSON_EVIDENCE
    return parsed


def canonical_gold_evidence(content: Any) -> str:
    visible_text = final_text_from_content(content)
    parsed = parsed_json_evidence(content)
    if parsed is INVALID_JSON_EVIDENCE:
        return "text:" + " ".join(visible_text.split())
    return "json:" + json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


_SENSITIVE_REPORT_TEXT = re.compile(
    r"""\b(?:
        authorization|proxy-authorization|bearer|set-cookie|cookies?|headers?|
        (?:x[-_])?api[-_]?key|api[-_ ]?key|access[-_ ]?key|
        (?:access|session|security)[-_ ]?tokens?|
        secrets?|credentials?|
        (?:[a-z0-9]+[-_])+[a-z0-9_-]*(?:token|key|secret|credential)[a-z0-9_-]*
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
_PROVIDER_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_STANDALONE_PROVIDER_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE)


def sanitize_report_text(value: Any) -> str | None:
    if value is None:
        return None
    message = str(value).strip()
    sensitive_starts = [
        match.start()
        for pattern in (_SENSITIVE_REPORT_TEXT, _PROVIDER_URL, _STANDALONE_PROVIDER_KEY)
        for match in [pattern.search(message)]
        if match is not None
    ]
    for variable in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL"):
        configured_value = os.getenv(variable)
        if configured_value and configured_value in message:
            sensitive_starts.append(message.index(configured_value))
    if not sensitive_starts:
        return message
    context = message[: min(sensitive_starts)].rstrip(" :;,-")
    return f"{context}: provider details redacted" if context else "provider details redacted"


def final_text(invocation: dict[str, Any]) -> str:
    return final_text_from_content(invocation.get("final_response"))


def case_user_text(case: dict[str, Any]) -> str:
    invocation = case["conversation"][0]
    parts = invocation["user_content"]["parts"]
    return "".join(str(part.get("text", "")) for part in parts).strip()


def case_expected_text(case: dict[str, Any]) -> str:
    return final_text(case["conversation"][0])


def case_tags(case: dict[str, Any]) -> list[str]:
    state = (case.get("session_input") or {}).get("state") or {}
    tags = state.get("tags") or []
    return [str(tag) for tag in tags]


def load_gate_config(
    path: Path | None = None,
    overrides: dict[str, Any] | None = None,
    optimizer_config: Path | None = None,
) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_GATE_CONFIG)
    required_source = "default"
    path_payload: dict[str, Any] = {}
    if path is not None:
        raw_path_payload = load_json(path)
        if not isinstance(raw_path_payload, Mapping):
            raise ValueError("gate config must be a JSON object")
        path_payload = dict(raw_path_payload)
        unknown_keys = sorted(str(key) for key in set(path_payload) - GATE_CONFIG_KEYS)
        if unknown_keys:
            raise ValueError("unknown gate config field(s): " + ", ".join(unknown_keys))
        config.update(path_payload)
        if "required_metrics" in path_payload:
            required_source = "gate_config"
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise ValueError("gate config overrides must be a mapping")
        override_payload = dict(overrides)
        unknown_keys = sorted(str(key) for key in set(override_payload) - GATE_CONFIG_KEYS)
        if unknown_keys:
            raise ValueError("unknown gate config override field(s): " + ", ".join(unknown_keys))
        config.update(override_payload)
        if "required_metrics" in overrides:
            required_source = "override"
    if optimizer_config is not None and required_source == "default":
        required, required_source = optimizer_required_metrics(optimizer_config)
        config["required_metrics"] = required
    elif optimizer_config is not None and config.get("required_metrics") == "all":
        config["required_metrics"] = optimizer_metric_names(optimizer_config)
    if config.get("required_metrics") is None:
        config["required_metrics"] = []
    config["required_metrics_source"] = required_source
    return config


def json_criteria_from_evaluation_config(metrics_config: Mapping[str, Any] | None) -> list[Any]:
    from trpc_agent_sdk.evaluation._eval_config import EvalConfig
    from trpc_agent_sdk.evaluation._eval_criterion import FinalResponseCriterion
    from trpc_agent_sdk.evaluation._eval_criterion import JSONCriterion

    criteria: list[JSONCriterion] = []

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                normalized_key = str(key).replace("_", "").lower()
                if normalized_key == "finalresponse" and isinstance(nested, Mapping):
                    final_response = FinalResponseCriterion.from_dict(dict(nested))
                    if final_response is not None and final_response.json_config is not None:
                        criteria.append(JSONCriterion.model_validate(final_response.json_config))
                elif normalized_key == "json" and isinstance(nested, Mapping):
                    criteria.append(JSONCriterion.model_validate(dict(nested)))
                else:
                    collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    if metrics_config is not None:
        eval_config = EvalConfig.model_validate(dict(metrics_config))
        for metric in eval_config.get_eval_metrics():
            collect(metric.criterion)
    return criteria or [JSONCriterion()]


def validate_inputs(
    train_evalset: Path,
    optimizer_dev_evalset: Path,
    val_evalset: Path,
    *,
    metrics_config: Mapping[str, Any] | None = None,
) -> None:
    paths = {
        "train": train_evalset,
        "optimizer_dev": optimizer_dev_evalset,
        "final_validation": val_evalset,
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)

    role_pairs = (("train", "optimizer_dev"), ("train", "final_validation"), ("optimizer_dev", "final_validation"))
    json_criteria = json_criteria_from_evaluation_config(metrics_config)
    for left_role, right_role in role_pairs:
        left = paths[left_role]
        right = paths[right_role]
        if left.resolve() == right.resolve() or os.path.samefile(left, right):
            raise ValueError(f"{left_role} and {right_role} evalsets resolve to the same file")
        if sha256_file(left) == sha256_file(right):
            raise ValueError(f"{left_role} and {right_role} evalsets are byte-identical copies")

    evidence: dict[str, dict[str, Any]] = {}
    for role, path in paths.items():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{role} evalset must be valid UTF-8 JSON: {error}") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("eval_cases"), list):
            raise ValueError(f"{role} evalset must be an object with an eval_cases array")
        if not payload["eval_cases"]:
            raise ValueError(f"{role} evalset eval_cases must not be empty")
        role_evidence = {"id": set(), "input": set(), "gold": set(), "json_gold": []}
        for position, case in enumerate(payload["eval_cases"]):
            prefix = f"{role} evalset eval_cases[{position}]"
            if not isinstance(case, dict):
                raise ValueError(f"{prefix} must be an object")
            case_id = case.get("eval_id")
            conversation = case.get("conversation")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError(f"{prefix}.eval_id must be a non-empty string")
            if case_id in role_evidence["id"]:
                raise ValueError(f"{role} evalset contains duplicate eval_id: {case_id}")
            if not isinstance(conversation, list) or not conversation:
                raise ValueError(f"{prefix}.conversation must be a non-empty array of objects")
            role_evidence["id"].add(case_id)
            for invocation_position, invocation in enumerate(conversation):
                invocation_prefix = f"{prefix}.conversation[{invocation_position}]"
                if not isinstance(invocation, dict):
                    raise ValueError(f"{invocation_prefix} must be an object")
                user_content = invocation.get("user_content")
                final_response = invocation.get("final_response")
                if not isinstance(user_content, dict) or not isinstance(user_content.get("parts"), list):
                    raise ValueError(f"{invocation_prefix} must contain user_content.parts as an array")
                if not isinstance(final_response, dict) or not isinstance(final_response.get("parts"), list):
                    raise ValueError(f"{invocation_prefix} must contain final_response.parts as an array")
                user_parts = user_content["parts"]
                gold_parts = final_response["parts"]
                if not user_parts or not all(
                    isinstance(part, dict) and isinstance(part.get("text"), str) for part in user_parts
                ):
                    raise ValueError(f"{invocation_prefix} user_content.parts must contain text strings")
                if not gold_parts or not all(
                    isinstance(part, dict) and isinstance(part.get("text"), str) for part in gold_parts
                ):
                    raise ValueError(f"{invocation_prefix} final_response.parts must contain text strings")
                normalized_input = " ".join("".join(part["text"] for part in user_parts).split()).casefold()
                visible_gold = final_text_from_content(final_response)
                if not normalized_input:
                    raise ValueError(f"{invocation_prefix} normalized user input must be non-empty")
                if not visible_gold:
                    raise ValueError(f"{invocation_prefix} visible final response must be non-empty")
                role_evidence["input"].add(normalized_input)
                role_evidence["gold"].add(canonical_gold_evidence(final_response))
                parsed_gold = parsed_json_evidence(final_response)
                if parsed_gold is not INVALID_JSON_EVIDENCE:
                    role_evidence["json_gold"].append(parsed_gold)
        evidence[role] = role_evidence

    for left_role, right_role in role_pairs:
        for evidence_type in ("id", "input", "gold"):
            overlap = evidence[left_role][evidence_type] & evidence[right_role][evidence_type]
            if overlap:
                raise ValueError(f"{left_role} and {right_role} evalsets overlap in {evidence_type} evidence")
        if any(
            criterion.matches(left_gold, right_gold)
            for criterion in json_criteria
            for left_gold in evidence[left_role]["json_gold"]
            for right_gold in evidence[right_role]["json_gold"]
        ):
            raise ValueError(f"{left_role} and {right_role} evalsets overlap in gold evidence")


def _is_windows_reserved_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    basename = value.rstrip(" .").split(".", 1)[0]
    return basename.casefold() in WINDOWS_RESERVED_BASENAMES


def _validate_safe_path_component(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SAFE_PATH_COMPONENT.fullmatch(value) is None or _is_windows_reserved_name(value):
        raise ValueError(f"{label} must be a safe single path component")
    return value


def _resolved_artifact_child(parent: Path, component: str, *, label: str) -> Path:
    safe_component = _validate_safe_path_component(component, label=label)
    resolved_parent = parent.resolve()
    child = resolved_parent / safe_component
    if child.is_symlink():
        raise ValueError(f"resolved {label} artifact path must not be a symlink")
    resolved_child = child.resolve()
    if not resolved_child.is_relative_to(resolved_parent):
        raise ValueError(f"resolved {label} artifact path must stay beneath its parent")
    return resolved_child


def _resolved_run_descendant(run_dir: Path, *components: str, label: str) -> Path:
    if run_dir.is_symlink():
        raise ValueError(f"resolved {label} must remain beneath run_dir and must not traverse a symlink")
    lexical_descendant = run_dir.joinpath(*components)
    try:
        relative_parts = lexical_descendant.relative_to(run_dir).parts
    except ValueError as error:
        raise ValueError(f"resolved {label} must remain beneath run_dir") from error
    cursor = run_dir
    for component in relative_parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise ValueError(f"resolved {label} must remain beneath run_dir and must not traverse a symlink")
    resolved_run_dir = run_dir.resolve()
    resolved_descendant = lexical_descendant.resolve()
    if not resolved_descendant.is_relative_to(resolved_run_dir):
        raise ValueError(f"resolved {label} must remain beneath run_dir")
    return resolved_descendant


def make_run_dir(output_dir: Path | None, run_id: str) -> Path:
    base = output_dir or DEFAULT_RUNS_DIR
    base = base.expanduser()
    if not base.is_absolute():
        base = (Path.cwd() / base).resolve()
    else:
        base = base.resolve()
    run_dir = _resolved_artifact_child(base, run_id, label="run_id")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def offline_metrics_path(run_dir: Path) -> Path:
    path = _resolved_run_descendant(run_dir, "offline_metrics.json", label="offline metrics artifact")
    write_json(path, OFFLINE_METRICS_CONFIG)
    return path


def online_metrics_path(run_dir: Path, optimizer_config: Path) -> Path:
    path = _resolved_run_descendant(run_dir, "online_eval_metrics.json", label="online metrics artifact")
    write_json(path, load_json(optimizer_config)["evaluate"])
    return path


def read_source_prompts(system_prompt: Path, router_prompt: Path) -> dict[str, tuple[Path, str]]:
    return {
        "system_prompt": (system_prompt, system_prompt.read_text(encoding="utf-8")),
        "router_prompt": (router_prompt, router_prompt.read_text(encoding="utf-8")),
    }


def build_prompt_target_manifest(
    source_prompts: Mapping[str, tuple[Path, str]],
) -> dict[str, dict[str, str]]:
    return {
        name: {"source_path": str(source_path), "sha256": sha256_text(source_text)}
        for name in PROMPT_TARGET_NAMES
        for source_path, source_text in [source_prompts[name]]
    }


def offline_candidate_prompts(
    source_prompts: dict[str, tuple[Path, str]],
    candidate_id: str,
    summary: str,
) -> dict[str, str]:
    prompts = {name: text for name, (_, text) in source_prompts.items()}
    if candidate_id != "baseline":
        prompts["router_prompt"] = (
            prompts["router_prompt"].rstrip() + "\n\n" + f"Offline candidate patch ({candidate_id}): {summary}\n"
        )
    return prompts


def prompt_diff(source: str, candidate: str, name: str) -> str:
    if source == candidate:
        return "unchanged"
    return "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"source/{name}.md",
            tofile=f"candidate/{name}.md",
        )
    )


def write_prompt_artifacts(
    *,
    run_dir: Path,
    candidate_id: str,
    source_prompts: dict[str, tuple[Path, str]],
    candidate_prompts: dict[str, str],
    summary: str,
    source_written: bool,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    safe_candidate_id = _validate_safe_path_component(candidate_id, label="candidate_id")
    prompt_dir = _resolved_run_descendant(
        run_dir,
        "prompts",
        safe_candidate_id,
        label="candidate prompt directory",
    )
    prompt_dir.mkdir(parents=True, exist_ok=True)
    audit: list[dict[str, Any]] = []
    patch_lines = [f"candidate: {candidate_id}", f"summary: {summary}", ""]
    for name, (source_path, source_text) in source_prompts.items():
        candidate_text = candidate_prompts.get(name, source_text)
        candidate_path = _resolved_run_descendant(
            run_dir,
            "prompts",
            safe_candidate_id,
            f"{name}.md",
            label="candidate prompt artifact",
        )
        candidate_path.write_text(candidate_text, encoding="utf-8")
        diff_text = prompt_diff(source_text, candidate_text, name)
        patch_lines.extend([f"## {name}", diff_text, ""])
        audit.append(
            {
                "name": name,
                "source_path": str(source_path),
                "candidate_path": str(candidate_path),
                "sha256": sha256_text(candidate_text),
                "content": candidate_text,
                "source_written": source_written,
                "summary": summary,
                "diff": diff_text,
            }
        )
    patch_path = _resolved_run_descendant(
        run_dir,
        "prompts",
        safe_candidate_id,
        "prompt_patch.diff",
        label="candidate prompt patch",
    )
    patch_path.write_text("\n".join(patch_lines), encoding="utf-8")
    return audit, {
        "prompt_dir": str(prompt_dir),
        "prompt_patch": str(patch_path),
    }


def _json_or_none(text: str) -> Any | None:
    try:
        parsed = json.loads((text or "").strip())
    except Exception:  # noqa: BLE001 - malformed model output becomes attribution.
        return None
    return parsed if isinstance(parsed, dict) else None


def _route_tool_args(value: Any) -> dict[str, Any] | None:
    parsed = _json_or_none(final_text_from_content(value))
    if not isinstance(parsed, dict):
        return None
    tool = parsed.get("tool")
    if not isinstance(tool, dict):
        return None
    if "route" not in parsed or "name" not in tool or "arguments" not in tool:
        return None
    arguments = tool.get("arguments")
    if not isinstance(arguments, dict):
        return None
    return {
        "route": str(parsed.get("route")),
        "tool": {
            "name": str(tool.get("name")),
            "arguments": arguments,
        },
    }


def route_tool_args_match(actual: Any, expected: Any) -> bool:
    """Compare router outputs by route, tool name, and arguments only."""

    actual_structured = _route_tool_args(actual)
    expected_structured = _route_tool_args(expected)
    return actual_structured is not None and actual_structured == expected_structured


_MISSING = object()


def _install_route_tool_args_metric():
    from trpc_agent_sdk.evaluation._evaluator_registry import EVALUATOR_REGISTRY
    from trpc_agent_sdk.evaluation._final_response_evaluator import FinalResponseEvaluator

    previous_evaluator = EVALUATOR_REGISTRY._registry.get(PRIMARY_METRIC, _MISSING)
    previous_compare = EVALUATOR_REGISTRY._criterion_compares.get(PRIMARY_METRIC, _MISSING)
    EVALUATOR_REGISTRY.register(PRIMARY_METRIC, FinalResponseEvaluator)
    EVALUATOR_REGISTRY.set_criterion_compare(PRIMARY_METRIC, route_tool_args_match)
    return previous_evaluator, previous_compare


def _restore_route_tool_args_metric(state: tuple[Any, Any]) -> None:
    from trpc_agent_sdk.evaluation._evaluator_registry import EVALUATOR_REGISTRY

    previous_evaluator, previous_compare = state
    if previous_evaluator is _MISSING:
        EVALUATOR_REGISTRY._registry.pop(PRIMARY_METRIC, None)
    else:
        EVALUATOR_REGISTRY.register(PRIMARY_METRIC, previous_evaluator)
    if previous_compare is _MISSING:
        EVALUATOR_REGISTRY._criterion_compares.pop(PRIMARY_METRIC, None)
    else:
        EVALUATOR_REGISTRY.set_criterion_compare(PRIMARY_METRIC, previous_compare)


def _metric_failed(metric: dict[str, Any]) -> bool:
    if "passed" in metric:
        return metric["passed"] is False
    return str(metric.get("status", "")).lower() == "failed"


def _failed_metric_names(metrics: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(name for name, metric in metrics.items() if _metric_failed(metric))


def _metric_failure_root(failed_metric_names: list[str]) -> tuple[str, str]:
    rubric = [name for name in failed_metric_names if "rubric" in name or name.startswith("llm_")]
    if rubric:
        return "rubric_failed", "rubric metric failed: " + ", ".join(rubric)
    knowledge = [
        name
        for name in failed_metric_names
        if any(token in name.lower() for token in ("knowledge", "retrieval", "recall", "ground"))
    ]
    if knowledge:
        return "knowledge_gap", "knowledge metric failed: " + ", ".join(knowledge)
    return "metric_failed", "content metric failed: " + ", ".join(failed_metric_names)


def attribute_failure_case(
    *,
    actual_text: str,
    expected_text: str,
    error_message: str | None,
    metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Classify one failed case using evaluator output and response shape."""

    if error_message:
        return {
            "root_cause": "runtime_error",
            "reasons": [f"evaluation runtime error: {error_message}"],
        }

    actual = _json_or_none(actual_text)
    expected = _json_or_none(expected_text)
    if actual is None:
        return {
            "root_cause": "format_error",
            "reasons": ["actual final response is not a valid JSON object"],
        }
    if expected is None:
        return {
            "root_cause": "runtime_error",
            "reasons": ["expected final response is not a valid JSON object"],
        }

    actual_tool = actual.get("tool")
    expected_tool = expected.get("tool")
    if not isinstance(expected_tool, dict):
        return {
            "root_cause": "runtime_error",
            "reasons": ["expected final response has a non-object tool field"],
        }
    if str(actual.get("route", "")) != str(expected.get("route", "")):
        return {
            "root_cause": "final_response_mismatch",
            "reasons": [
                "actual route " f"{actual.get('route')!r} did not match expected route {expected.get('route')!r}"
            ],
        }
    if not isinstance(actual_tool, dict):
        return {
            "root_cause": "tool_call_error",
            "reasons": ["actual tool must be a JSON object"],
        }
    if str(actual_tool.get("name", "")) != str(expected_tool.get("name", "")):
        return {
            "root_cause": "tool_call_error",
            "reasons": [
                "actual tool " f"{actual_tool.get('name')!r} did not match expected tool {expected_tool.get('name')!r}"
            ],
        }
    actual_arguments = actual_tool.get("arguments", _MISSING)
    expected_arguments = expected_tool.get("arguments", _MISSING)
    if not isinstance(expected_arguments, dict):
        return {
            "root_cause": "runtime_error",
            "reasons": ["expected tool arguments must be a JSON object"],
        }
    if not isinstance(actual_arguments, dict):
        return {
            "root_cause": "parameter_error",
            "reasons": ["actual tool arguments must be a JSON object"],
        }
    if actual_arguments != expected_arguments:
        return {
            "root_cause": "parameter_error",
            "reasons": ["tool arguments did not match expected arguments"],
        }

    failed_metric_names = _failed_metric_names(metrics)
    if failed_metric_names:
        root_cause, reason = _metric_failure_root(failed_metric_names)
        return {"root_cause": root_cause, "reasons": [reason]}
    return {
        "root_cause": "metric_failed",
        "reasons": ["case failed without a reported failed metric"],
    }


def _status_name(status: Any) -> str:
    return str(getattr(status, "name", status)).lower()


def _is_passed_status(status: Any) -> bool:
    return _status_name(status) == "passed"


def _extract_actual_expected(run: Any, case: dict[str, Any]) -> tuple[str, str]:
    actual_text = ""
    expected_text = case_expected_text(case)
    if run.eval_metric_result_per_invocation:
        invocation = run.eval_metric_result_per_invocation[0]
        actual_text = final_text_from_content(invocation.actual_invocation.final_response)
        if invocation.expected_invocation is not None:
            expected_text = final_text_from_content(invocation.expected_invocation.final_response)
    return actual_text, expected_text


def summarize_evaluate_result(result: Any, evalset_payload: dict[str, Any]) -> dict[str, Any]:
    case_by_id = {case["eval_id"]: case for case in evalset_payload["eval_cases"]}
    eval_set_id, set_result = next(iter(result.results_by_eval_set_id.items()))
    case_results: list[dict[str, Any]] = []

    for case in evalset_payload["eval_cases"]:
        eval_id = case["eval_id"]
        runs = set_result.eval_results_by_eval_id.get(eval_id, [])
        if not runs:
            metrics: dict[str, dict[str, Any]] = {}
            expected_text = case_expected_text(case)
            error_message = "AgentEvaluator returned no run for case"
            attribution = attribute_failure_case(
                actual_text="",
                expected_text=expected_text,
                error_message=error_message,
                metrics=metrics,
            )
            case_results.append(
                {
                    "case_id": eval_id,
                    "tags": case_tags(case),
                    "user": case_user_text(case),
                    "score": 0.0,
                    "passed": False,
                    "metrics": metrics,
                    "actual_text": "",
                    "expected_text": expected_text,
                    "key_trace": {
                        "invocation_id": str(case_by_id[eval_id]["conversation"][0].get("invocation_id", "")),
                        "actual_final_response": "",
                        "expected_final_response": expected_text,
                        "error_message": error_message,
                    },
                    "root_cause": attribution["root_cause"],
                    "reasons": attribution["reasons"],
                }
            )
            continue

        run_scores: list[float] = []
        run_passed = True
        metric_run_scores: dict[str, list[float]] = {}
        metric_evidence: dict[str, dict[str, Any]] = {}
        actual_text, expected_text = _extract_actual_expected(runs[0], case_by_id[eval_id])
        error_message = None
        for run in runs:
            run_metrics = list(run.overall_eval_metric_results or [])
            run_passed = (
                run_passed
                and _is_passed_status(run.final_eval_status)
                and bool(run_metrics)
                and all(_is_passed_status(metric.eval_status) for metric in run_metrics)
            )
            if run.error_message and error_message is None:
                error_message = sanitize_report_text(run.error_message)
            for metric in run_metrics:
                score = metric.score
                metric_passed = _is_passed_status(metric.eval_status)
                details = getattr(metric, "details", None)
                reason = getattr(details, "reason", None) if details is not None else None
                threshold = float(metric.threshold)
                if score is not None:
                    metric_run_scores.setdefault(metric.metric_name, []).append(float(score))
                metric_evidence[metric.metric_name] = {
                    "score": None if score is None else float(score),
                    "threshold": threshold,
                    "status": _status_name(metric.eval_status),
                    "passed": metric_passed,
                    "reason": sanitize_report_text(reason),
                }
                if metric.metric_name == PRIMARY_METRIC and score is not None:
                    run_scores.append(float(score))

        merged_metrics: dict[str, dict[str, Any]] = {}
        for metric_name, evidence in metric_evidence.items():
            scores = metric_run_scores.get(metric_name, [])
            if scores:
                average_score = sum(scores) / len(scores)
                threshold = evidence["threshold"]
                passed = average_score >= threshold
                merged_metrics[metric_name] = {
                    **evidence,
                    "score": round(average_score, 6),
                    "passed": passed,
                    "status": "passed" if passed else "failed",
                }
            else:
                merged_metrics[metric_name] = evidence

        if not run_scores:
            run_scores = [
                float(metric["score"]) for metric in merged_metrics.values() if metric.get("score") is not None
            ]
        score_value = sum(run_scores) / len(run_scores) if run_scores else (1.0 if run_passed else 0.0)
        attribution = {"root_cause": "", "reasons": []}
        if not run_passed:
            attribution = attribute_failure_case(
                actual_text=actual_text,
                expected_text=expected_text,
                error_message=error_message,
                metrics=merged_metrics,
            )
        case_results.append(
            {
                "case_id": eval_id,
                "tags": case_tags(case),
                "user": case_user_text(case),
                "score": round(score_value, 6),
                "passed": run_passed,
                "metrics": merged_metrics,
                "actual_text": actual_text,
                "expected_text": expected_text,
                "key_trace": {
                    "invocation_id": str(case_by_id[eval_id]["conversation"][0].get("invocation_id", "")),
                    "actual_final_response": actual_text,
                    "expected_final_response": expected_text,
                    "error_message": error_message,
                },
                "root_cause": attribution["root_cause"],
                "reasons": attribution["reasons"],
            }
        )

    total = len(case_results)
    score = sum(item["score"] for item in case_results) / total if total else 0.0
    pass_rate = sum(1 for item in case_results if item["passed"]) / total if total else 0.0
    metric_scores: dict[str, list[float]] = {}
    metric_thresholds: dict[str, float] = {}
    for case_result in case_results:
        for metric_name, metric in case_result["metrics"].items():
            metric_score = _finite_float(metric.get("score"))
            metric_threshold = _finite_float(metric.get("threshold"))
            if metric_score is not None:
                metric_scores.setdefault(metric_name, []).append(metric_score)
            if metric_threshold is not None:
                metric_thresholds[metric_name] = metric_threshold
    metrics_summary: dict[str, dict[str, Any]] = {}
    for name, scores in metric_scores.items():
        threshold = metric_thresholds.get(name, 1.0)
        avg = sum(scores) / len(scores) if scores else 0.0
        metrics_summary[name] = {
            "score": round(avg, 6),
            "threshold": threshold,
            "passed": avg >= threshold,
            "status": "passed" if avg >= threshold else "failed",
        }

    return {
        "eval_set_id": eval_set_id,
        "score": round(score, 6),
        "pass_rate": round(pass_rate, 6),
        "metrics": metrics_summary,
        "case_results": case_results,
        "failed_case_ids": [item["case_id"] for item in case_results if not item["passed"]],
        "source": "AgentEvaluator",
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=str)]
    return value


def attribution_for(evaluation: dict[str, Any]) -> dict[str, Any]:
    case_results = evaluation.get("case_results")
    if not isinstance(case_results, list):
        case_results = []
    failed = [case for case in case_results if isinstance(case, dict) and case.get("passed") is not True]
    counts = Counter({name: 0 for name in TAXONOMY})
    cases = []
    for case in failed:
        root = case.get("root_cause") or "runtime_error"
        if root not in TAXONOMY:
            root = "runtime_error"
        counts[root] += 1
        reasons = case.get("reasons") or ["no failure reason recorded"]
        cases.append(
            {
                "case_id": str(case.get("case_id", "")),
                "root_cause": root,
                "score": _finite_float(case.get("score")),
                "reasons": reasons if isinstance(reasons, list) else [str(reasons)],
            }
        )
    covered = sum(1 for case in cases if case["reasons"])
    return {
        "coverage": round(covered / len(failed), 6) if failed else 1.0,
        "taxonomy_counts": {name: counts[name] for name in TAXONOMY},
        "cases": cases,
    }


def _offline_rubric_score(actual_text: str) -> tuple[float, str]:
    parsed = _json_or_none(actual_text)
    if parsed is None:
        return 0.0, "offline rubric failed: final response is not a JSON object"
    if not parsed.get("route"):
        return 0.0, "offline rubric failed: route is missing"
    if not isinstance(parsed.get("tool"), dict):
        return 0.0, "offline rubric failed: tool is missing or not an object"
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return 0.0, "offline rubric failed: reason is missing"
    unsafe_terms = ("guarantee", "guaranteed", "promise", "approved refund", "instant credit")
    if any(term in reason.lower() for term in unsafe_terms):
        return 0.0, "offline rubric failed: reason makes an unsafe commitment"
    return 1.0, "offline rubric passed"


def _install_offline_rubric_evaluator():
    from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric
    from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
    from trpc_agent_sdk.evaluation._eval_result import EvaluationResult
    from trpc_agent_sdk.evaluation._eval_result import PerInvocationResult
    from trpc_agent_sdk.evaluation._evaluator_base import Evaluator
    from trpc_agent_sdk.evaluation._evaluator_registry import EVALUATOR_REGISTRY

    previous = EVALUATOR_REGISTRY.get_evaluator_class(EvalMetric(metric_name=OFFLINE_RUBRIC_METRIC, threshold=1.0))

    class OfflineRubricEvaluator(Evaluator):
        requires_reference = False

        def __init__(self, eval_metric: Any | None = None) -> None:
            self._threshold = float(getattr(eval_metric, "threshold", 1.0) or 1.0)

        async def evaluate_invocations(self, actual_invocations, expected_invocations):
            per_invocation_results = []
            scores = []
            for actual in actual_invocations:
                score, reason = _offline_rubric_score(final_text_from_content(actual.final_response))
                scores.append(score)
                per_invocation_results.append(
                    PerInvocationResult(
                        actual_invocation=actual,
                        expected_invocation=None,
                        score=score,
                        eval_status=EvalStatus.PASSED if score >= self._threshold else EvalStatus.FAILED,
                        reason=reason,
                    )
                )
            overall = sum(scores) / len(scores) if scores else 0.0
            return EvaluationResult(
                overall_score=overall,
                overall_eval_status=EvalStatus.PASSED if overall >= self._threshold else EvalStatus.FAILED,
                per_invocation_results=per_invocation_results,
            )

    EVALUATOR_REGISTRY.register(OFFLINE_RUBRIC_METRIC, OfflineRubricEvaluator)
    return previous


async def run_evaluator(
    *,
    evalset_path: Path,
    evalset_payload: dict[str, Any],
    metrics_path: Path,
    call_agent: Callable[[str], Awaitable[str]] | None = None,
    offline_rubric: bool = False,
) -> dict[str, Any]:
    from trpc_agent_sdk.evaluation import AgentEvaluator
    from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
    from trpc_agent_sdk.evaluation._evaluator_registry import EVALUATOR_REGISTRY

    old_cwd = os.getcwd()
    route_metric_state = _install_route_tool_args_metric()
    previous_rubric_evaluator = _install_offline_rubric_evaluator() if offline_rubric else None
    os.chdir(evalset_path.parent)
    try:
        executer = AgentEvaluator.get_executer(
            evalset_path.name,
            call_agent=call_agent,
            eval_metrics_file_path_or_dir=str(metrics_path),
            print_detailed_results=False,
            print_summary_report=False,
        )
        try:
            await executer.evaluate()
        except _EvaluationCasesFailed:
            pass
        result = executer.get_result()
        if result is None:
            raise RuntimeError(f"AgentEvaluator produced no result for {evalset_path}")
        return summarize_evaluate_result(result, evalset_payload)
    finally:
        if previous_rubric_evaluator is not None:
            EVALUATOR_REGISTRY.register(OFFLINE_RUBRIC_METRIC, previous_rubric_evaluator)
        _restore_route_tool_args_metric(route_metric_state)
        os.chdir(old_cwd)


def make_fixture_call_agent(
    evalset_payload: dict[str, Any],
    outputs: dict[str, str],
) -> Callable[[str], Awaitable[str]]:
    query_to_output = {case_user_text(case): outputs.get(case["eval_id"], "") for case in evalset_payload["eval_cases"]}

    async def call_agent(query: str) -> str:
        return query_to_output.get(query, "")

    return call_agent


def materialize_trace_evalset(
    *,
    source_evalset: Path,
    payload: dict[str, Any],
    outputs: dict[str, str],
    run_dir: Path,
    candidate_id: str,
    split: str,
) -> tuple[Path, dict[str, Any]]:
    _validate_safe_path_component(candidate_id, label="candidate_id")
    trace_payload = copy.deepcopy(payload)
    trace_payload["eval_set_id"] = f"{payload['eval_set_id']}_{candidate_id}_{split}_trace"
    trace_payload["description"] = (
        f"Trace replay for {candidate_id} {split}, generated by eval_optimize_loop/run_pipeline.py"
    )
    for case in trace_payload["eval_cases"]:
        case["eval_mode"] = "trace"
        expected_invocation = copy.deepcopy(case["conversation"][0])
        actual_invocation = copy.deepcopy(expected_invocation)
        actual_invocation["final_response"] = {
            "parts": [{"text": outputs.get(case["eval_id"], "")}],
            "role": "model",
        }
        case["actual_conversation"] = [actual_invocation]
    trace_name = f"{candidate_id}.{split}.trace.evalset.json"
    _validate_safe_path_component(trace_name, label="trace artifact name")
    path = _resolved_run_descendant(
        run_dir,
        "evalsets",
        trace_name,
        label="trace evalset artifact",
    )
    write_json(path, trace_payload)
    if candidate_id == "candidate_local_patch" and split == "validation":
        trace_alias_path = _resolved_run_descendant(
            run_dir,
            "trace_evalset.json",
            label="trace evalset alias artifact",
        )
        write_json(trace_alias_path, trace_payload)
    return path, trace_payload


async def evaluate_fixture_split(
    *,
    mode: str,
    split: str,
    candidate_id: str,
    evalset_path: Path,
    evalset_payload: dict[str, Any],
    outputs: dict[str, str],
    run_dir: Path,
    metrics_path: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    artifacts: dict[str, str] = {}
    if mode == "trace":
        trace_path, trace_payload = materialize_trace_evalset(
            source_evalset=evalset_path,
            payload=evalset_payload,
            outputs=outputs,
            run_dir=run_dir,
            candidate_id=candidate_id,
            split=split,
        )
        artifacts[f"{split}_trace_evalset"] = str(trace_path)
        summary = await run_evaluator(
            evalset_path=trace_path,
            evalset_payload=trace_payload,
            metrics_path=metrics_path,
            offline_rubric=True,
        )
        return summary, artifacts

    call_agent = make_fixture_call_agent(evalset_payload, outputs)
    summary = await run_evaluator(
        evalset_path=evalset_path,
        evalset_payload=evalset_payload,
        metrics_path=metrics_path,
        call_agent=call_agent,
        offline_rubric=True,
    )
    return summary, artifacts


def classify_case_delta(before: dict[str, Any], after: dict[str, Any]) -> str:
    if not bool(before.get("passed")) and bool(after.get("passed")):
        return "new_pass"
    if bool(before.get("passed")) and not bool(after.get("passed")):
        return "new_fail"
    before_score = _finite_float(before.get("score"))
    after_score = _finite_float(after.get("score"))
    if before_score is None or after_score is None:
        return "unchanged"
    if after_score > before_score:
        return "score_improved"
    if after_score < before_score:
        return "score_regressed"
    return "unchanged"


def build_case_deltas(baseline_val: dict[str, Any], candidate_val: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_by_id, _ = _index_gate_cases(baseline_val)
    candidate_by_id, _ = _index_gate_cases(candidate_val)
    deltas = []
    for case_id in sorted(set(baseline_by_id) | set(candidate_by_id)):
        before = baseline_by_id.get(case_id)
        after = candidate_by_id.get(case_id)
        baseline_score = None if before is None else _finite_float(before.get("score"))
        candidate_score = None if after is None else _finite_float(after.get("score"))
        delta = (
            None if baseline_score is None or candidate_score is None else round(candidate_score - baseline_score, 6)
        )
        if before is None:
            root_cause = "unexpected_candidate"
            change_type = "unexpected_candidate"
            reasons = ["candidate introduced an unknown validation case"]
        elif after is None:
            root_cause = "missing_candidate"
            change_type = "missing_candidate"
            reasons = ["candidate omitted a baseline validation case"]
        else:
            root_cause = after.get("root_cause", "")
            change_type = classify_case_delta(before, after)
            reasons = after.get("reasons", [])
        deltas.append(
            {
                "case_id": case_id,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
                "baseline_passed": None if before is None else bool(before.get("passed")),
                "candidate_passed": None if after is None else bool(after.get("passed")),
                "delta": delta,
                "change_type": change_type,
                "baseline_actual_text": "" if before is None else before.get("actual_text", ""),
                "candidate_actual_text": "" if after is None else after.get("actual_text", ""),
                "root_cause": root_cause,
                "reasons": reasons,
            }
        )
    return deltas


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _score_delta(candidate: Any, baseline: Any) -> float | None:
    candidate_score = _finite_float(candidate)
    baseline_score = _finite_float(baseline)
    if candidate_score is None or baseline_score is None:
        return None
    delta = candidate_score - baseline_score
    return round(delta, 6) if math.isfinite(delta) else None


def _index_gate_cases(
    evaluation: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    cases = evaluation.get("case_results")
    if not isinstance(cases, list):
        return {}, ["case_results must be an array"]
    indexed: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for position, case in enumerate(cases):
        if not isinstance(case, dict):
            issues.append(f"case_results[{position}] must be an object")
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            issues.append(f"case_results[{position}] case_id must be a non-empty string")
            continue
        if case_id in indexed:
            issues.append(f"duplicate case_id: {case_id}")
            continue
        indexed[case_id] = case
    return indexed, issues


def _case_derived_summary_score(evaluation: Mapping[str, Any]) -> float | None:
    cases = evaluation.get("case_results")
    if not isinstance(cases, list) or not cases:
        return None
    scores: list[float] = []
    for case in cases:
        if not isinstance(case, Mapping):
            return None
        score = _finite_float(case.get("score"))
        if score is None or not 0 <= score <= 1:
            return None
        scores.append(score)
    return round(sum(scores) / len(scores), 6)


def _normalized_gate_tags(case: dict[str, Any]) -> set[str]:
    tags = case.get("tags", [])
    if not isinstance(tags, list):
        return set()
    return {str(tag).lower() for tag in tags}


def _normalize_required_metrics(value: Any, candidate_metrics: dict[str, Any]) -> tuple[list[str], str | None]:
    if value == "all":
        names = [name for name in candidate_metrics if isinstance(name, str) and name.strip()]
        if not names or len(names) != len(candidate_metrics):
            return [], "required_metrics='all' requires a non-empty object with string metric names"
        return sorted(names), None
    if not isinstance(value, list):
        return [], "required_metrics must be 'all' or an array of non-empty unique strings"
    if any(not isinstance(name, str) or not name.strip() for name in value):
        return [], "required_metrics must contain only non-empty strings"
    if len(set(value)) != len(value):
        return [], "required_metrics must contain unique strings"
    return value, None


def apply_gate(
    *,
    candidate_id: str,
    baseline_val: Any,
    candidate_val: Any,
    gate_config: Any,
    duration_seconds: float,
    cost_usd: float | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if isinstance(baseline_val, Mapping):
        normalized_baseline = dict(baseline_val)
    else:
        normalized_baseline = {}
        reasons.append("baseline_val must be a mapping")
    if isinstance(candidate_val, Mapping):
        normalized_candidate = dict(candidate_val)
    else:
        normalized_candidate = {}
        reasons.append("candidate_val must be a mapping")
    if isinstance(gate_config, Mapping):
        normalized_gate_config = dict(gate_config)
    else:
        normalized_gate_config = {}
        reasons.append("gate_config must be a mapping")

    unknown_gate_keys = sorted(str(key) for key in set(normalized_gate_config) - GATE_RUNTIME_KEYS)
    if unknown_gate_keys:
        reasons.append("unknown gate config field(s): " + ", ".join(unknown_gate_keys))

    baseline_by_id, baseline_issues = _index_gate_cases(normalized_baseline)
    candidate_by_id, candidate_issues = _index_gate_cases(normalized_candidate)
    reasons.extend(baseline_issues)
    reasons.extend(candidate_issues)
    accepted = not reasons

    allow_new_hard_fails = normalized_gate_config.get("allow_new_hard_fails", False)
    if not isinstance(allow_new_hard_fails, bool):
        allow_new_hard_fails = False
        accepted = False
        reasons.append("allow_new_hard_fails must be a boolean and was treated as false")
    allow_critical_regression = normalized_gate_config.get("allow_critical_regression", False)
    if not isinstance(allow_critical_regression, bool):
        allow_critical_regression = False
        accepted = False
        reasons.append("allow_critical_regression must be a boolean and was treated as false")

    for label, cases in (("baseline", baseline_by_id), ("candidate", candidate_by_id)):
        for case_id, case in cases.items():
            score = _finite_float(case.get("score"))
            if score is None or not 0 <= score <= 1:
                accepted = False
                reasons.append(f"{label} case {case_id} score must be a finite number in [0, 1]")
            if not isinstance(case.get("passed"), bool):
                accepted = False
                reasons.append(f"{label} case {case_id} passed must be a boolean")
            if not isinstance(case.get("tags", []), list):
                accepted = False
                reasons.append(f"{label} case {case_id} tags must be an array")

    for label, summary in (("baseline", normalized_baseline), ("candidate", normalized_candidate)):
        expected_summary_score = _case_derived_summary_score(summary)
        reported_summary_score = _finite_float(summary.get("score"))
        if expected_summary_score is None:
            accepted = False
            reasons.append(f"{label} summary score could not be derived from non-empty valid case results")
        elif reported_summary_score is not None and round(reported_summary_score, 6) != expected_summary_score:
            accepted = False
            reasons.append(
                f"{label} summary score {reported_summary_score:.6f} does not match "
                f"case-derived score {expected_summary_score:.6f}"
            )

    baseline_score = _finite_float(normalized_baseline.get("score"))
    candidate_score = _finite_float(normalized_candidate.get("score"))
    raw_validation_delta = (
        None if baseline_score is None or candidate_score is None else candidate_score - baseline_score
    )
    validation_delta = (
        raw_validation_delta if raw_validation_delta is not None and math.isfinite(raw_validation_delta) else None
    )
    if baseline_score is not None and not 0 <= baseline_score <= 1:
        baseline_score = None
    if candidate_score is not None and not 0 <= candidate_score <= 1:
        candidate_score = None
    if baseline_score is None or candidate_score is None:
        validation_delta = None
    if validation_delta is None:
        accepted = False
        reasons.append("baseline and candidate validation scores must be finite numbers")
    min_delta = _finite_float(normalized_gate_config.get("min_validation_delta", 0.0))
    if min_delta is None or min_delta < 0:
        accepted = False
        reasons.append("minimum validation delta must be a finite non-negative number")
    if validation_delta is not None and validation_delta <= 0:
        accepted = False
        reasons.append("validation score did not improve over baseline")
    elif validation_delta is not None and min_delta is not None and min_delta >= 0 and validation_delta <= min_delta:
        accepted = False
        reasons.append(
            "validation score improvement " f"{validation_delta:.4f} must be greater than required {min_delta:.4f}"
        )

    baseline_ids = set(baseline_by_id)
    candidate_ids = set(candidate_by_id)
    missing_case_ids = sorted(baseline_ids - candidate_ids)
    unexpected_case_ids = sorted(candidate_ids - baseline_ids)
    if missing_case_ids:
        accepted = False
        reasons.append("candidate omitted validation case(s): " + ", ".join(missing_case_ids))
    if unexpected_case_ids:
        accepted = False
        reasons.append("candidate introduced unknown validation case(s): " + ", ".join(unexpected_case_ids))

    common_case_ids = sorted(baseline_ids & candidate_ids)
    tag_mismatch_ids = [
        case_id
        for case_id in common_case_ids
        if _normalized_gate_tags(baseline_by_id[case_id]) != _normalized_gate_tags(candidate_by_id[case_id])
    ]
    if tag_mismatch_ids:
        accepted = False
        reasons.append("baseline/candidate tag mismatch for validation case(s): " + ", ".join(tag_mismatch_ids))
    new_hard_fail_ids = [
        case_id
        for case_id in common_case_ids
        if baseline_by_id[case_id].get("passed") and not candidate_by_id[case_id].get("passed")
    ]
    if new_hard_fail_ids and not allow_new_hard_fails:
        accepted = False
        reasons.append("candidate introduced hard fail(s): " + ", ".join(new_hard_fail_ids))

    critical_regression_ids = [
        case_id
        for case_id in common_case_ids
        if "critical" in _normalized_gate_tags(baseline_by_id[case_id])
        and _finite_float(candidate_by_id[case_id].get("score")) is not None
        and _finite_float(baseline_by_id[case_id].get("score")) is not None
        and _finite_float(candidate_by_id[case_id].get("score")) < _finite_float(baseline_by_id[case_id].get("score"))
    ]
    if critical_regression_ids and not allow_critical_regression:
        accepted = False
        reasons.append("candidate regressed critical case(s): " + ", ".join(critical_regression_ids))

    normalized_cost = None if cost_usd is None else _finite_float(cost_usd)
    if cost_usd is not None and (normalized_cost is None or normalized_cost < 0):
        normalized_cost = None
        accepted = False
        reasons.append("run cost must be a finite non-negative number")

    max_cost = normalized_gate_config.get("max_cost_usd")
    normalized_max_cost = None if max_cost is None else _finite_float(max_cost)
    if max_cost is not None and (normalized_max_cost is None or normalized_max_cost < 0):
        normalized_max_cost = None
        accepted = False
        reasons.append("cost budget must be a finite non-negative number")
    elif max_cost is not None and cost_usd is None:
        accepted = False
        reasons.append("cost budget could not be evaluated because run cost is unknown")
    elif max_cost is not None and normalized_cost is not None and normalized_cost > normalized_max_cost:
        accepted = False
        reasons.append(f"run exceeded cost budget: {normalized_cost:.4f} > {normalized_max_cost:.4f} USD")

    max_seconds = normalized_gate_config.get("max_duration_seconds")
    normalized_duration = _finite_float(duration_seconds)
    normalized_max_seconds = None if max_seconds is None else _finite_float(max_seconds)
    if normalized_duration is None or normalized_duration < 0:
        normalized_duration = None
        accepted = False
        reasons.append("run duration must be a finite non-negative number")
    elif max_seconds is not None and (normalized_max_seconds is None or normalized_max_seconds < 0):
        normalized_max_seconds = None
        accepted = False
        reasons.append("duration budget must be a finite non-negative number")
    elif max_seconds is not None and normalized_duration > normalized_max_seconds:
        accepted = False
        reasons.append(f"run exceeded duration budget: {normalized_duration:.2f}s > {normalized_max_seconds:.2f}s")

    candidate_metrics = normalized_candidate.get("metrics")
    if not isinstance(candidate_metrics, dict):
        candidate_metrics = {}
        accepted = False
        reasons.append("candidate metrics must be an object")
    required, required_issue = _normalize_required_metrics(
        normalized_gate_config.get("required_metrics", []),
        candidate_metrics,
    )
    if required_issue:
        accepted = False
        reasons.append(required_issue)
    missing_or_failed = []
    for name in required:
        metric = candidate_metrics.get(name)
        metric_consistent = isinstance(metric, dict) and metric.get("passed") is True
        if metric_consistent and ("score" in metric or "threshold" in metric):
            metric_score = _finite_float(metric.get("score"))
            metric_threshold = _finite_float(metric.get("threshold"))
            metric_consistent = (
                metric_score is not None
                and 0 <= metric_score <= 1
                and metric_threshold is not None
                and metric_threshold >= 0
                and metric_score >= metric_threshold
            )
        if metric_consistent and "status" in metric:
            metric_consistent = str(metric.get("status", "")).lower() == "passed"
        if not metric_consistent:
            missing_or_failed.append(name)
    if missing_or_failed:
        accepted = False
        reasons.append("required metric(s) missing or failing: " + ", ".join(missing_or_failed))

    if accepted:
        reasons.append("validation improved and all configured gates passed")

    return {
        "candidate_id": candidate_id,
        "accepted": accepted,
        "reasons": reasons,
        "new_hard_fail_ids": new_hard_fail_ids,
        "critical_regression_ids": critical_regression_ids,
        "missing_case_ids": missing_case_ids,
        "unexpected_case_ids": unexpected_case_ids,
        "validation_delta": None if validation_delta is None else round(validation_delta, 6),
    }


def gate_candidate(
    *,
    candidate_id: str,
    baseline_val: dict[str, Any],
    candidate_val: dict[str, Any],
    duration_seconds: float,
    max_seconds: float,
) -> dict[str, Any]:
    gate_config = copy.deepcopy(DEFAULT_GATE_CONFIG)
    gate_config["max_duration_seconds"] = max_seconds
    return apply_gate(
        candidate_id=candidate_id,
        baseline_val=baseline_val,
        candidate_val=candidate_val,
        gate_config=gate_config,
        duration_seconds=duration_seconds,
        cost_usd=0.0,
    )


def build_candidate_report(
    *,
    candidate_id: str,
    fixture: dict[str, Any],
    train: dict[str, Any],
    optimizer_dev: dict[str, Any],
    validation: dict[str, Any],
    baseline_train: dict[str, Any],
    baseline_optimizer_dev: dict[str, Any],
    baseline_val: dict[str, Any],
    gate_config: dict[str, Any],
    duration_seconds: float,
    gate_duration_seconds: float | None = None,
    cost_usd: float | None,
    seed: int,
    optimizer_config: Path,
    prompt_artifacts: list[dict[str, Any]] | None = None,
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    observed_gate_duration = round(
        duration_seconds if gate_duration_seconds is None else gate_duration_seconds,
        6,
    )
    gate = apply_gate(
        candidate_id=candidate_id,
        baseline_val=baseline_val,
        candidate_val=validation,
        gate_config=gate_config,
        duration_seconds=observed_gate_duration,
        cost_usd=cost_usd,
    )
    return _json_safe(
        {
            "id": candidate_id,
            "audit": build_candidate_audit(
                seed=seed,
                duration_seconds=duration_seconds,
                cost_usd=cost_usd,
                optimizer_config=optimizer_config,
            ),
            "prompt_patch_summary": fixture.get("prompt_patch_summary", ""),
            "prompt_artifacts": prompt_artifacts or [],
            "train": _json_safe(train),
            "optimizer_dev": _json_safe(optimizer_dev),
            "final_validation": _json_safe(validation),
            "validation": _json_safe(validation),
            "delta": {
                "train_score": _score_delta(train.get("score"), baseline_train.get("score")),
                "optimizer_dev_score": _score_delta(optimizer_dev.get("score"), baseline_optimizer_dev.get("score")),
                "validation_score": _score_delta(validation.get("score"), baseline_val.get("score")),
            },
            "case_deltas": build_case_deltas(baseline_val, validation),
            "gate": gate,
            "failure_attribution": attribution_for(validation),
            "artifacts": artifacts or {},
        }
    )


def pick_winner(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    accepted = [candidate for candidate in candidates if candidate["gate"]["accepted"]]
    if not accepted:
        return None
    return max(
        accepted,
        key=lambda candidate: (
            candidate["validation"]["score"],
            candidate["train"]["score"],
            candidate["id"],
        ),
    )


def common_artifacts(
    *,
    run_dir: Path,
    train_evalset: Path,
    optimizer_dev_evalset: Path,
    val_evalset: Path,
    optimizer_config: Path,
    fixture_path: Path,
    metrics_path: Path,
    system_prompt: Path,
    router_prompt: Path,
) -> dict[str, str]:
    return {
        "optimization_report_json": str(run_dir / "optimization_report.json"),
        "optimization_report_md": str(run_dir / "optimization_report.md"),
        "train_evalset": str(train_evalset),
        "optimizer_dev_evalset": str(optimizer_dev_evalset),
        "validation_evalset": str(val_evalset),
        "final_validation_evalset": str(val_evalset),
        "optimizer_config": str(optimizer_config),
        "fixtures": str(fixture_path),
        "eval_metrics": str(metrics_path),
        "system_prompt": str(system_prompt),
        "router_prompt": str(router_prompt),
    }


def existing_artifact(path: Path) -> str:
    return str(path) if path.exists() else ""


def build_top_level_report(
    *,
    mode: str,
    run_id: str,
    run_dir: Path,
    seed: int,
    baseline_fixture: dict[str, Any],
    baseline_train: dict[str, Any],
    baseline_optimizer_dev: dict[str, Any],
    baseline_val: dict[str, Any],
    baseline_prompt_artifacts: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    gate_config: dict[str, Any],
    artifacts: dict[str, Any],
    cost: dict[str, Any],
    duration_seconds: float,
    config_snapshot: dict[str, Any],
    command: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    winner = pick_winner(candidates)
    if winner is None:
        rejection_reasons = ["no candidate passed all gates"]
        for candidate in candidates:
            for reason in candidate["gate"]["reasons"]:
                rejection_reasons.append(f"{candidate['id']}: {reason}")
        gate_decision = {
            "accepted": False,
            "winner": None,
            "reasons": rejection_reasons,
        }
        delta = {
            "validation_score": 0.0,
            "optimizer_dev_score": 0.0,
            "train_score": 0.0,
        }
    else:
        gate_decision = {
            "accepted": True,
            "winner": winner["id"],
            "reasons": winner["gate"]["reasons"],
        }
        delta = winner["delta"]

    report = {
        "run_id": run_id,
        "mode": mode,
        "seed": seed,
        "baseline": {
            "prompt_patch_summary": baseline_fixture.get("prompt_patch_summary", ""),
            "prompt_artifacts": baseline_prompt_artifacts,
            "train": baseline_train,
            "optimizer_dev": baseline_optimizer_dev,
            "final_validation": baseline_val,
            "validation": baseline_val,
        },
        "candidates": candidates,
        "delta": delta,
        "gate_decision": gate_decision,
        "failure_attribution": attribution_for(baseline_val),
        "cost": cost,
        "duration_seconds": round(duration_seconds, 6),
        "optimization_rounds": [],
        "config_snapshot": config_snapshot,
        "environment_snapshot": environment_snapshot(
            seed=seed,
            command=command,
            config_path=str((config_snapshot.get("paths") or {}).get("optimizer_config", "")),
        ),
        "artifacts": artifacts,
    }
    if extra:
        report.update(extra)
    return report


def make_report(
    *,
    mode: str,
    run_id: str,
    run_dir: Path,
    seed: int,
    started: float,
    extra_artifacts: dict[str, str] | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    report = asyncio.run(
        build_offline_report(
            mode=mode,
            run_id=run_id,
            run_dir=run_dir,
            seed=seed,
            started=started,
            train_evalset=TRAIN_PATH.resolve(),
            optimizer_dev_evalset=OPTIMIZER_DEV_PATH.resolve(),
            val_evalset=VAL_PATH.resolve(),
            optimizer_config=OPTIMIZER_CONFIG_PATH.resolve(),
            fixture_path=(TRACE_FIXTURE_PATH if mode == "trace" else FIXTURE_PATH).resolve(),
            gate_config=load_gate_config(optimizer_config=OPTIMIZER_CONFIG_PATH.resolve()),
            system_prompt=SYSTEM_PROMPT_PATH.resolve(),
            router_prompt=ROUTER_PROMPT_PATH.resolve(),
            command=command,
        )
    )
    if extra_artifacts:
        report["artifacts"].update(extra_artifacts)
    return report


async def build_offline_report(
    *,
    mode: str,
    run_id: str,
    run_dir: Path,
    seed: int,
    started: float,
    train_evalset: Path,
    optimizer_dev_evalset: Path,
    val_evalset: Path,
    optimizer_config: Path,
    fixture_path: Path,
    gate_config: dict[str, Any],
    system_prompt: Path,
    router_prompt: Path,
    command: str | None = None,
) -> dict[str, Any]:
    train_payload = load_json(train_evalset)
    optimizer_dev_payload = load_json(optimizer_dev_evalset)
    val_payload = load_json(val_evalset)
    fixtures = load_json(fixture_path)
    for candidate_id in fixtures:
        _validate_safe_path_component(candidate_id, label="candidate_id")
    metrics_path = offline_metrics_path(run_dir)
    source_prompts = read_source_prompts(system_prompt, router_prompt)
    if mode == "trace":
        trace_metrics_path = _resolved_run_descendant(
            run_dir,
            "trace_metrics.json",
            label="trace metrics artifact",
        )
        write_json(trace_metrics_path, OFFLINE_METRICS_CONFIG)

    baseline_fixture = fixtures["baseline"]
    baseline_train, baseline_train_artifacts = await evaluate_fixture_split(
        mode=mode,
        split="train",
        candidate_id="baseline",
        evalset_path=train_evalset,
        evalset_payload=train_payload,
        outputs=baseline_fixture["outputs"],
        run_dir=run_dir,
        metrics_path=metrics_path,
    )
    baseline_val, baseline_val_artifacts = await evaluate_fixture_split(
        mode=mode,
        split="validation",
        candidate_id="baseline",
        evalset_path=val_evalset,
        evalset_payload=val_payload,
        outputs=baseline_fixture["outputs"],
        run_dir=run_dir,
        metrics_path=metrics_path,
    )
    baseline_optimizer_dev, baseline_optimizer_dev_artifacts = await evaluate_fixture_split(
        mode=mode,
        split="optimizer_dev",
        candidate_id="baseline",
        evalset_path=optimizer_dev_evalset,
        evalset_payload=optimizer_dev_payload,
        outputs=baseline_fixture["outputs"],
        run_dir=run_dir,
        metrics_path=metrics_path,
    )
    baseline_prompt_artifacts, baseline_prompt_paths = write_prompt_artifacts(
        run_dir=run_dir,
        candidate_id="baseline",
        source_prompts=source_prompts,
        candidate_prompts=offline_candidate_prompts(
            source_prompts,
            "baseline",
            baseline_fixture.get("prompt_patch_summary", ""),
        ),
        summary=baseline_fixture.get("prompt_patch_summary", ""),
        source_written=False,
    )

    candidates: list[dict[str, Any]] = []
    for candidate_id, fixture in fixtures.items():
        if candidate_id == "baseline":
            continue
        candidate_started = time.perf_counter()
        train, train_artifacts = await evaluate_fixture_split(
            mode=mode,
            split="train",
            candidate_id=candidate_id,
            evalset_path=train_evalset,
            evalset_payload=train_payload,
            outputs=fixture["outputs"],
            run_dir=run_dir,
            metrics_path=metrics_path,
        )
        optimizer_dev, optimizer_dev_artifacts = await evaluate_fixture_split(
            mode=mode,
            split="optimizer_dev",
            candidate_id=candidate_id,
            evalset_path=optimizer_dev_evalset,
            evalset_payload=optimizer_dev_payload,
            outputs=fixture["outputs"],
            run_dir=run_dir,
            metrics_path=metrics_path,
        )
        validation, val_artifacts = await evaluate_fixture_split(
            mode=mode,
            split="validation",
            candidate_id=candidate_id,
            evalset_path=val_evalset,
            evalset_payload=val_payload,
            outputs=fixture["outputs"],
            run_dir=run_dir,
            metrics_path=metrics_path,
        )
        candidate_artifacts = {}
        candidate_artifacts.update(train_artifacts)
        candidate_artifacts.update(optimizer_dev_artifacts)
        candidate_artifacts.update(val_artifacts)
        prompt_artifacts, prompt_paths = write_prompt_artifacts(
            run_dir=run_dir,
            candidate_id=candidate_id,
            source_prompts=source_prompts,
            candidate_prompts=offline_candidate_prompts(
                source_prompts,
                candidate_id,
                fixture.get("prompt_patch_summary", ""),
            ),
            summary=fixture.get("prompt_patch_summary", ""),
            source_written=False,
        )
        candidate_artifacts.update(prompt_paths)
        candidate_duration_seconds = time.perf_counter() - candidate_started
        candidates.append(
            build_candidate_report(
                candidate_id=candidate_id,
                fixture=fixture,
                train=train,
                optimizer_dev=optimizer_dev,
                validation=validation,
                baseline_train=baseline_train,
                baseline_optimizer_dev=baseline_optimizer_dev,
                baseline_val=baseline_val,
                gate_config=gate_config,
                duration_seconds=candidate_duration_seconds,
                cost_usd=0.0,
                seed=seed,
                optimizer_config=optimizer_config,
                prompt_artifacts=prompt_artifacts,
                artifacts=candidate_artifacts,
            )
        )

    artifacts = common_artifacts(
        run_dir=run_dir,
        train_evalset=train_evalset,
        optimizer_dev_evalset=optimizer_dev_evalset,
        val_evalset=val_evalset,
        optimizer_config=optimizer_config,
        fixture_path=fixture_path,
        metrics_path=metrics_path,
        system_prompt=system_prompt,
        router_prompt=router_prompt,
    )
    if mode == "trace":
        artifacts.update(
            {
                "trace_evalset": str(run_dir / "trace_evalset.json"),
                "trace_metrics": str(run_dir / "trace_metrics.json"),
            }
        )
    artifacts.update(
        {
            "baseline_train_trace_evalset": baseline_train_artifacts.get("train_trace_evalset", ""),
            "baseline_optimizer_dev_trace_evalset": baseline_optimizer_dev_artifacts.get(
                "optimizer_dev_trace_evalset", ""
            ),
            "baseline_validation_trace_evalset": baseline_val_artifacts.get("validation_trace_evalset", ""),
            "baseline_prompt_dir": baseline_prompt_paths.get("prompt_dir", ""),
            "baseline_prompt_patch": baseline_prompt_paths.get("prompt_patch", ""),
        }
    )
    evaluation_snapshot = normalized_evaluation_config(OFFLINE_METRICS_CONFIG)

    return build_top_level_report(
        mode=mode,
        run_id=run_id,
        run_dir=run_dir,
        seed=seed,
        baseline_fixture=baseline_fixture,
        baseline_train=baseline_train,
        baseline_optimizer_dev=baseline_optimizer_dev,
        baseline_val=baseline_val,
        baseline_prompt_artifacts=baseline_prompt_artifacts,
        candidates=candidates,
        gate_config=gate_config,
        artifacts=artifacts,
        cost={
            "currency": "USD",
            "estimated_total": 0.0,
            "cost_source": "deterministic_offline",
            "unknown_cost_reason": None,
            "model_calls": 0,
            "token_usage": {
                "prompt": 0,
                "completion": 0,
                "total": 0,
            },
            "token_usage_known": True,
            "unknown_token_usage_reason": None,
            "optimizer": {
                "estimated_cost": 0.0,
                "model_calls": 0,
                "candidate_evaluation_agent_calls": 0,
                "reflection_lm_calls": 0,
                "judge_calls_per_candidate_evaluation": 0,
                "judge_model_calls": 0,
                "native_judge_model_calls": 0,
                "derived_judge_model_calls": 0,
                "judge_model_call_source": "none",
                "token_usage": {
                    "prompt": 0,
                    "completion": 0,
                    "total": 0,
                },
                "token_usage_known": True,
                "unknown_token_usage_reason": None,
                "usage_evidence_valid": True,
                "reflection_reported_usage": {
                    "estimated_cost": 0.0,
                    "token_usage": {
                        "prompt": 0,
                        "completion": 0,
                        "total": 0,
                    },
                    "token_usage_known": True,
                    "unknown_token_usage_reason": None,
                },
            },
            "final_revalidation": {
                "estimated_cost": 0.0,
                "agent_calls_per_run": 0,
                "agent_calls": 0,
                "judge_calls_per_agent_call": 0,
                "judge_model_calls": 0,
                "model_calls": 0,
                "token_usage": {
                    "prompt": 0,
                    "completion": 0,
                    "total": 0,
                },
                "token_usage_known": True,
                "unknown_token_usage_reason": None,
            },
        },
        duration_seconds=time.perf_counter() - started,
        config_snapshot={
            "mode": mode,
            "seed": seed,
            "gate": gate_config,
            "evaluation": evaluation_snapshot,
            "evaluation_sha256": sha256_json(evaluation_snapshot),
            "evaluation_metrics_sha256": sha256_json_file(metrics_path),
            "optimizer_config_sha256": sha256_json_file(optimizer_config),
            "prompt_targets": build_prompt_target_manifest(source_prompts),
            "evalsets": build_evalset_manifests(train_evalset, optimizer_dev_evalset, val_evalset),
            "paths": {
                "train_evalset": str(train_evalset),
                "optimizer_dev_evalset": str(optimizer_dev_evalset),
                "validation_evalset": str(val_evalset),
                "final_validation_evalset": str(val_evalset),
                "optimizer_config": str(optimizer_config),
                "evaluation_metrics": str(metrics_path),
                "fixture_outputs": str(fixture_path),
                "system_prompt": str(system_prompt),
                "router_prompt": str(router_prompt),
            },
        },
        command=command,
    )


def _make_llm_agent_from_prompts(prompt_texts: dict[str, str]):
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.models import OpenAIModel

    from agent.config import get_model_config

    api_key, base_url, model_name = get_model_config()
    instruction = "\n\n".join(
        [
            prompt_texts.get("system_prompt", "").strip(),
            prompt_texts.get("router_prompt", "").strip(),
        ]
    )
    return LlmAgent(
        name="support_router_agent",
        model=OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
        ),
        instruction=instruction,
    )


def make_online_call_agent(
    *,
    system_prompt: Path,
    router_prompt: Path,
    prompt_texts: dict[str, str] | None = None,
) -> Callable[[str], Awaitable[str]]:
    async def call_agent(query: str) -> str:
        from trpc_agent_sdk.runners import Runner
        from trpc_agent_sdk.sessions import InMemorySessionService
        from trpc_agent_sdk.types import Content
        from trpc_agent_sdk.types import Part

        prompts = prompt_texts or {
            "system_prompt": system_prompt.read_text(encoding="utf-8"),
            "router_prompt": router_prompt.read_text(encoding="utf-8"),
        }
        root_agent = _make_llm_agent_from_prompts(prompts)
        session_service = InMemorySessionService()
        runner = Runner(
            app_name="support_router_optimizer",
            agent=root_agent,
            session_service=session_service,
        )
        user_id = "optimizer"
        session_id = str(uuid.uuid4())
        try:
            await session_service.create_session(
                app_name="support_router_optimizer",
                user_id=user_id,
                session_id=session_id,
                state={},
            )
            final = ""
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=Content(role="user", parts=[Part.from_text(text=query)]),
            ):
                if not event.is_final_response() or not event.content:
                    continue
                for part in event.content.parts or []:
                    if part.thought:
                        continue
                    if part.text:
                        final += part.text
        except BaseException:
            try:
                await runner.close()
            except BaseException:
                logger.exception("Failed to close online evaluation runner after a primary error.")
            raise
        else:
            await runner.close()
        return final.strip()

    return call_agent


async def online_call_agent(query: str) -> str:
    return await make_online_call_agent(
        system_prompt=SYSTEM_PROMPT_PATH,
        router_prompt=ROUTER_PROMPT_PATH,
    )(query)


def _optimizer_fixture(result: Any) -> dict[str, str]:
    return {"prompt_patch_summary": "Best prompt returned by AgentOptimizer.optimize(update_source=False)."}


def _optimizer_extra(result: Any) -> dict[str, Any]:
    return {
        "online_result": {
            "status": result.status,
            "error_message": sanitize_report_text(getattr(result, "error_message", "")),
            "baseline_pass_rate": result.baseline_pass_rate,
            "best_pass_rate": result.best_pass_rate,
            "pass_rate_improvement": result.pass_rate_improvement,
            "stop_reason": result.stop_reason,
            "baseline_metric_breakdown": getattr(result, "baseline_metric_breakdown", {}),
            "best_metric_breakdown": getattr(result, "best_metric_breakdown", {}),
        }
    }


def _count_attr(obj: Any, name: str) -> tuple[int, bool]:
    value = getattr(obj, name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0, True
    return value, False


def _is_llm_metric(metric: dict[str, Any]) -> bool:
    name = str(metric.get("metric_name") or metric.get("metricName") or "")
    criterion = metric.get("criterion") or {}
    return name.startswith("llm_") or "llm_judge" in criterion or "llmJudge" in criterion


def judge_calls_per_agent_call(metrics_config: Mapping[str, Any]) -> int:
    from trpc_agent_sdk.evaluation._eval_config import EvalConfig
    from trpc_agent_sdk.evaluation._llm_criterion import get_llm_criterion_from_metric

    try:
        eval_config = EvalConfig.model_validate(dict(metrics_config))
    except Exception as error:
        raise ValueError(f"invalid evaluation metrics config: {error}") from error

    total = 0
    for metric in eval_config.get_eval_metrics():
        criterion = get_llm_criterion_from_metric(metric)
        if criterion is not None:
            judge_models = criterion.get_judge_models()
            if not judge_models:
                raise ValueError(f"LLM metric {metric.metric_name} has no judge model")
            total += sum(model.get_num_samples() for model in judge_models)
        elif str(metric.metric_name).startswith("llm_"):
            total += 1
    return total


def final_revalidation_call_audit(
    summaries: list[dict[str, Any]],
    metrics_config: dict[str, Any],
    *,
    evalset_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    num_runs, invalid_num_runs = _normalized_round_count(metrics_config.get("num_runs", 1))
    if invalid_num_runs or num_runs <= 0:
        raise ValueError("evaluation num_runs must be a positive integer")
    if evalset_payloads is None:
        agent_calls_per_run = sum(len(summary.get("case_results", [])) for summary in summaries)
    else:
        if len(evalset_payloads) != len(summaries):
            raise ValueError("final revalidation payload count must match summary count")
        agent_calls_per_run = 0
        for payload in evalset_payloads:
            cases = payload.get("eval_cases") if isinstance(payload, dict) else None
            if not isinstance(cases, list):
                raise ValueError("final revalidation evalset payload must contain eval_cases")
            for case in cases:
                conversation = case.get("conversation") if isinstance(case, dict) else None
                if not isinstance(conversation, list) or not conversation:
                    raise ValueError("final revalidation case must contain a non-empty conversation")
                agent_calls_per_run += len(conversation)
    case_runs = agent_calls_per_run * num_runs
    judge_multiplier = judge_calls_per_agent_call(metrics_config)
    judge_calls = case_runs * judge_multiplier
    return {
        "agent_calls_per_run": agent_calls_per_run,
        "agent_calls": case_runs,
        "judge_calls_per_agent_call": judge_multiplier,
        "judge_model_calls": judge_calls,
        "model_calls": case_runs + judge_calls,
    }


def online_cost_audit(
    result: Any,
    *,
    optimizer_candidate_agent_calls: int,
    final_revalidation_calls: dict[str, int],
    optimizer_judge_calls_per_agent_call: int | None = None,
    optimizer_llm_metric_count: int | None = None,
) -> dict[str, Any]:
    reflection_calls, invalid_reflection_calls = _count_attr(result, "total_reflection_lm_calls")
    native_judge_calls, invalid_judge_calls = _count_attr(result, "total_judge_model_calls")
    candidate_calls, invalid_candidate_calls = _normalized_round_count(optimizer_candidate_agent_calls)
    legacy_multiplier_conflict = False
    if optimizer_judge_calls_per_agent_call is None:
        optimizer_judge_calls_per_agent_call = optimizer_llm_metric_count or 0
    elif optimizer_llm_metric_count is not None and optimizer_llm_metric_count != optimizer_judge_calls_per_agent_call:
        legacy_multiplier_conflict = True
    judge_multiplier, invalid_judge_multiplier = _normalized_round_count(optimizer_judge_calls_per_agent_call)
    derived_judge_calls = candidate_calls * judge_multiplier
    judge_calls = max(native_judge_calls, derived_judge_calls)
    if native_judge_calls and derived_judge_calls:
        judge_call_source = (
            "native_and_derived_agree"
            if native_judge_calls == derived_judge_calls
            else "reconciled_native_and_derived_max"
        )
    elif native_judge_calls:
        judge_call_source = "native_optimizer_counter"
    elif derived_judge_calls:
        judge_call_source = "derived_from_candidate_calls_and_llm_metrics"
    else:
        judge_call_source = "none"
    optimizer_calls = candidate_calls + reflection_calls + judge_calls
    token_usage, invalid_token_usage = _normalized_token_usage(getattr(result, "total_token_usage", {}))
    raw_cost = getattr(result, "total_llm_cost", None)
    reflection_cost = _finite_float(raw_cost)
    invalid_cost = reflection_cost is None or reflection_cost < 0
    if invalid_cost:
        reflection_cost = None

    malformed_native_usage = (
        invalid_reflection_calls
        or invalid_judge_calls
        or invalid_candidate_calls
        or invalid_judge_multiplier
        or legacy_multiplier_conflict
        or invalid_token_usage
        or invalid_cost
    )
    phase_usage_known = not malformed_native_usage and candidate_calls == 0 and judge_calls == 0
    optimizer_cost = reflection_cost if phase_usage_known else None
    optimizer_token_usage = token_usage if phase_usage_known else None

    total_cost = optimizer_cost
    unknown_reasons: list[str] = []
    if candidate_calls > 0:
        unknown_reasons.append("optimizer candidate-evaluation calls do not expose token or cost usage")
    if judge_calls > 0:
        unknown_reasons.append("optimizer judge calls do not expose token or cost usage")
    if malformed_native_usage:
        unknown_reasons.append("optimizer native usage counters were malformed and normalized fail-closed")
    if final_revalidation_calls["model_calls"] > 0:
        unknown_reasons.append("final revalidation model calls are not provider-priced by AgentEvaluator")
        total_cost = None

    final_tokens_known = final_revalidation_calls["model_calls"] == 0
    final_token_usage = {name: 0 for name in TOKEN_USAGE_KEYS} if final_tokens_known else None
    pipeline_tokens_known = phase_usage_known and final_tokens_known
    pipeline_token_usage = optimizer_token_usage if pipeline_tokens_known else None
    optimizer_token_unknown_reasons = []
    if candidate_calls > 0:
        optimizer_token_unknown_reasons.append("optimizer candidate-evaluation token usage is not exposed")
    if judge_calls > 0:
        optimizer_token_unknown_reasons.append("optimizer judge token usage is not exposed")
    if malformed_native_usage:
        optimizer_token_unknown_reasons.append("optimizer native usage counters were malformed")
    token_unknown_reasons = list(optimizer_token_unknown_reasons)
    if not final_tokens_known:
        token_unknown_reasons.append("AgentEvaluator does not expose final revalidation token usage")

    return {
        "currency": "USD",
        "estimated_total": total_cost,
        "cost_source": "unknown" if unknown_reasons else "optimizer_result",
        "unknown_cost_reason": "; ".join(unknown_reasons) if unknown_reasons else None,
        "model_calls": optimizer_calls + final_revalidation_calls["model_calls"],
        "token_usage": pipeline_token_usage,
        "token_usage_known": pipeline_tokens_known,
        "unknown_token_usage_reason": "; ".join(token_unknown_reasons) if token_unknown_reasons else None,
        "optimizer": {
            "estimated_cost": optimizer_cost,
            "model_calls": optimizer_calls,
            "candidate_evaluation_agent_calls": candidate_calls,
            "reflection_lm_calls": reflection_calls,
            "judge_calls_per_candidate_evaluation": judge_multiplier,
            "judge_model_calls": judge_calls,
            "native_judge_model_calls": native_judge_calls,
            "derived_judge_model_calls": derived_judge_calls,
            "judge_model_call_source": judge_call_source,
            "token_usage": optimizer_token_usage,
            "token_usage_known": phase_usage_known,
            "unknown_token_usage_reason": (None if phase_usage_known else "; ".join(optimizer_token_unknown_reasons)),
            "usage_evidence_valid": not malformed_native_usage,
            "reflection_reported_usage": {
                "estimated_cost": reflection_cost,
                "token_usage": token_usage,
                "token_usage_known": not invalid_token_usage,
                "unknown_token_usage_reason": (
                    "malformed optimizer-reported reflection token usage" if invalid_token_usage else None
                ),
            },
        },
        "final_revalidation": {
            "estimated_cost": 0.0 if final_revalidation_calls["model_calls"] == 0 else None,
            **final_revalidation_calls,
            "token_usage": final_token_usage,
            "token_usage_known": final_tokens_known,
            "unknown_token_usage_reason": (
                None if final_tokens_known else "AgentEvaluator does not expose token usage"
            ),
        },
    }


async def run_fake_or_trace(
    *,
    mode: str,
    seed: int,
    output_dir: Path | None,
    run_id: str | None,
    train_evalset: Path | None = None,
    optimizer_dev_evalset: Path | None = None,
    val_evalset: Path | None = None,
    optimizer_config: Path | None = None,
    fixture_outputs: Path | None = None,
    gate_config_path: Path | None = None,
    gate_config: dict[str, Any] | None = None,
    system_prompt: Path | None = None,
    router_prompt: Path | None = None,
    command: str | None = None,
) -> Path:
    started = time.perf_counter()
    actual_run_id = run_id or datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = make_run_dir(output_dir, actual_run_id)
    train_path = resolve_path(train_evalset, TRAIN_PATH)
    optimizer_dev_path = resolve_path(optimizer_dev_evalset, OPTIMIZER_DEV_PATH)
    val_path = resolve_path(val_evalset, VAL_PATH)
    optimizer_path = resolve_path(optimizer_config, OPTIMIZER_CONFIG_PATH)
    fixture_default = TRACE_FIXTURE_PATH if mode == "trace" else FIXTURE_PATH
    fixture_path = resolve_path(fixture_outputs, fixture_default)
    system_path = resolve_path(system_prompt, SYSTEM_PROMPT_PATH)
    router_path = resolve_path(router_prompt, ROUTER_PROMPT_PATH)
    optimizer_evaluate_config = validated_optimizer_evaluate_config(optimizer_path)
    validate_inputs(
        train_path,
        optimizer_dev_path,
        val_path,
        metrics_config=optimizer_evaluate_config,
    )
    gate = load_gate_config(gate_config_path, gate_config, optimizer_config=optimizer_path)

    report = await build_offline_report(
        mode=mode,
        run_id=actual_run_id,
        run_dir=run_dir,
        seed=seed,
        started=started,
        train_evalset=train_path,
        optimizer_dev_evalset=optimizer_dev_path,
        val_evalset=val_path,
        optimizer_config=optimizer_path,
        fixture_path=fixture_path,
        gate_config=gate,
        system_prompt=system_path,
        router_prompt=router_path,
        command=command,
    )
    write_report(run_dir, report)
    return run_dir


async def run_online(
    *,
    seed: int,
    output_dir: Path | None,
    run_id: str | None,
    train_evalset: Path | None = None,
    optimizer_dev_evalset: Path | None = None,
    val_evalset: Path | None = None,
    optimizer_config: Path | None = None,
    gate_config_path: Path | None = None,
    gate_config: dict[str, Any] | None = None,
    system_prompt: Path | None = None,
    router_prompt: Path | None = None,
    command: str | None = None,
) -> Path:
    from agent.config import get_model_config

    preflight = require_online_preflight()
    print(format_online_preflight(preflight))
    get_model_config()
    from trpc_agent_sdk.evaluation import AgentOptimizer
    from trpc_agent_sdk.evaluation import TargetPrompt

    started = time.perf_counter()
    actual_run_id = run_id or datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = make_run_dir(output_dir, actual_run_id)
    train_path = resolve_path(train_evalset, TRAIN_PATH)
    optimizer_dev_path = resolve_path(optimizer_dev_evalset, OPTIMIZER_DEV_PATH)
    val_path = resolve_path(val_evalset, VAL_PATH)
    optimizer_path = resolve_path(optimizer_config, OPTIMIZER_CONFIG_PATH)
    system_path = resolve_path(system_prompt, SYSTEM_PROMPT_PATH)
    router_path = resolve_path(router_prompt, ROUTER_PROMPT_PATH)
    source_evaluate_config = validated_optimizer_evaluate_config(optimizer_path)
    validate_inputs(
        train_path,
        optimizer_dev_path,
        val_path,
        metrics_config=source_evaluate_config,
    )
    runtime_optimizer_path = materialize_optimizer_config(
        run_dir=run_dir,
        source_config=optimizer_path,
        seed=seed,
    )
    optimizer_evaluate_config = validated_optimizer_evaluate_config(runtime_optimizer_path)
    optimizer_judge_multiplier = judge_calls_per_agent_call(optimizer_evaluate_config)
    gate = load_gate_config(gate_config_path, gate_config, optimizer_config=runtime_optimizer_path)
    source_prompts = read_source_prompts(system_path, router_path)

    online_dir = _resolved_run_descendant(
        run_dir,
        "online",
        label="online optimizer artifact directory",
    )
    if online_dir.exists():
        raise ValueError("online optimizer artifact directory must not already exist")
    online_dir.mkdir()
    target = TargetPrompt().add_path("system_prompt", str(system_path)).add_path("router_prompt", str(router_path))
    optimizer_candidate_agent_calls = 0
    optimizer_call_agent = make_online_call_agent(system_prompt=system_path, router_prompt=router_path)

    async def counted_optimizer_call_agent(query: str) -> str:
        nonlocal optimizer_candidate_agent_calls
        optimizer_candidate_agent_calls += 1
        return await optimizer_call_agent(query)

    route_metric_state = _install_route_tool_args_metric()
    try:
        result = await AgentOptimizer.optimize(
            config_path=str(runtime_optimizer_path),
            call_agent=counted_optimizer_call_agent,
            target_prompt=target,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(optimizer_dev_path),
            output_dir=str(online_dir),
            update_source=False,
            verbose=0,
        )
    finally:
        _restore_route_tool_args_metric(route_metric_state)
    optimization_finished = time.perf_counter()

    train_payload = load_json(train_path)
    optimizer_dev_payload = load_json(optimizer_dev_path)
    val_payload = load_json(val_path)
    metrics_path = online_metrics_path(run_dir, runtime_optimizer_path)
    source_prompt_texts = {name: text for name, (_, text) in source_prompts.items()}
    best_prompt_texts = {
        **source_prompt_texts,
        **dict(getattr(result, "best_prompts", {}) or {}),
    }
    baseline_call_agent = make_online_call_agent(system_prompt=system_path, router_prompt=router_path)
    best_call_agent = make_online_call_agent(
        system_prompt=system_path,
        router_prompt=router_path,
        prompt_texts=best_prompt_texts,
    )
    baseline_train = await run_evaluator(
        evalset_path=train_path,
        evalset_payload=train_payload,
        metrics_path=metrics_path,
        call_agent=baseline_call_agent,
    )
    baseline_val = await run_evaluator(
        evalset_path=val_path,
        evalset_payload=val_payload,
        metrics_path=metrics_path,
        call_agent=baseline_call_agent,
    )
    baseline_optimizer_dev = await run_evaluator(
        evalset_path=optimizer_dev_path,
        evalset_payload=optimizer_dev_payload,
        metrics_path=metrics_path,
        call_agent=baseline_call_agent,
    )
    baseline_finished = time.perf_counter()
    candidate_started = baseline_finished
    best_train = await run_evaluator(
        evalset_path=train_path,
        evalset_payload=train_payload,
        metrics_path=metrics_path,
        call_agent=best_call_agent,
    )
    best_optimizer_dev = await run_evaluator(
        evalset_path=optimizer_dev_path,
        evalset_payload=optimizer_dev_payload,
        metrics_path=metrics_path,
        call_agent=best_call_agent,
    )
    best_val = await run_evaluator(
        evalset_path=val_path,
        evalset_payload=val_payload,
        metrics_path=metrics_path,
        call_agent=best_call_agent,
    )
    candidate_finished = time.perf_counter()
    candidate_duration_seconds = candidate_finished - candidate_started
    gate_elapsed_seconds = candidate_finished - started

    metrics_config = load_json(metrics_path)
    cost = online_cost_audit(
        result,
        optimizer_candidate_agent_calls=optimizer_candidate_agent_calls,
        optimizer_judge_calls_per_agent_call=optimizer_judge_multiplier,
        final_revalidation_calls=final_revalidation_call_audit(
            [
                baseline_train,
                baseline_optimizer_dev,
                baseline_val,
                best_train,
                best_optimizer_dev,
                best_val,
            ],
            metrics_config,
            evalset_payloads=[
                train_payload,
                optimizer_dev_payload,
                val_payload,
                train_payload,
                optimizer_dev_payload,
                val_payload,
            ],
        ),
    )
    cost_usd = cost["estimated_total"]
    baseline_prompt_artifacts, baseline_prompt_paths = write_prompt_artifacts(
        run_dir=run_dir,
        candidate_id="baseline",
        source_prompts=source_prompts,
        candidate_prompts={name: text for name, (_, text) in source_prompts.items()},
        summary="Source prompts before AgentOptimizer.optimize.",
        source_written=False,
    )
    best_prompt_artifacts, best_prompt_paths = write_prompt_artifacts(
        run_dir=run_dir,
        candidate_id="optimizer_best",
        source_prompts=source_prompts,
        candidate_prompts=best_prompt_texts,
        summary="Best prompt returned by AgentOptimizer.optimize(update_source=False).",
        source_written=False,
    )
    optimization_rounds = write_optimizer_round_artifacts(
        run_dir=run_dir,
        rounds=list(getattr(result, "rounds", []) or []),
    )
    candidate = build_candidate_report(
        candidate_id="optimizer_best",
        fixture=_optimizer_fixture(result),
        train=best_train,
        optimizer_dev=best_optimizer_dev,
        validation=best_val,
        baseline_train=baseline_train,
        baseline_optimizer_dev=baseline_optimizer_dev,
        baseline_val=baseline_val,
        gate_config=gate,
        duration_seconds=candidate_duration_seconds,
        gate_duration_seconds=gate_elapsed_seconds,
        cost_usd=cost_usd,
        seed=seed,
        optimizer_config=runtime_optimizer_path,
        prompt_artifacts=best_prompt_artifacts,
        artifacts={
            "native_optimizer_dir": existing_artifact(online_dir),
            "native_result_json": existing_artifact(online_dir / "result.json"),
            "native_summary_txt": existing_artifact(online_dir / "summary.txt"),
            "native_rounds_dir": existing_artifact(online_dir / "rounds"),
            "native_baseline_prompts_dir": existing_artifact(online_dir / "baseline_prompts"),
            "native_best_prompts_dir": existing_artifact(online_dir / "best_prompts"),
            "native_best_prompts": existing_artifact(online_dir / "best_prompts"),
            "native_config_snapshot_json": existing_artifact(online_dir / "config.snapshot.json"),
            **best_prompt_paths,
        },
    )
    if result.status != "SUCCEEDED":
        candidate["gate"]["accepted"] = False
        candidate["gate"]["reasons"].append(f"native optimizer status was {result.status}")
        error_message = sanitize_report_text(getattr(result, "error_message", ""))
        if error_message:
            candidate["gate"]["reasons"].append(error_message)
    if not cost["optimizer"]["usage_evidence_valid"]:
        candidate["gate"]["accepted"] = False
        candidate["gate"]["reasons"].append("optimizer usage evidence was malformed and cannot be audited")

    artifacts = common_artifacts(
        run_dir=run_dir,
        train_evalset=train_path,
        optimizer_dev_evalset=optimizer_dev_path,
        val_evalset=val_path,
        optimizer_config=runtime_optimizer_path,
        fixture_path=FIXTURE_PATH,
        metrics_path=metrics_path,
        system_prompt=system_path,
        router_prompt=router_path,
    )
    artifacts.update(candidate["artifacts"])
    artifacts.update(
        {
            "online_eval_metrics": str(metrics_path),
            "optimizer_source_config": str(optimizer_path),
            "optimizer_runtime_config": str(runtime_optimizer_path),
            "baseline_prompt_dir": baseline_prompt_paths.get("prompt_dir", ""),
            "baseline_prompt_patch": baseline_prompt_paths.get("prompt_patch", ""),
        }
    )
    evaluation_snapshot = normalized_evaluation_config(optimizer_evaluate_config)
    report = build_top_level_report(
        mode="online",
        run_id=actual_run_id,
        run_dir=run_dir,
        seed=seed,
        baseline_fixture={"prompt_patch_summary": "Source prompts before AgentOptimizer.optimize."},
        baseline_train=baseline_train,
        baseline_optimizer_dev=baseline_optimizer_dev,
        baseline_val=baseline_val,
        baseline_prompt_artifacts=baseline_prompt_artifacts,
        candidates=[candidate],
        gate_config=gate,
        artifacts=artifacts,
        cost=cost,
        duration_seconds=time.perf_counter() - started,
        config_snapshot={
            "mode": "online",
            "seed": seed,
            "gate": gate,
            "evaluation": evaluation_snapshot,
            "evaluation_sha256": sha256_json(evaluation_snapshot),
            "evaluation_metrics_sha256": sha256_json_file(metrics_path),
            "optimizer_config_sha256": sha256_json_file(runtime_optimizer_path),
            "prompt_targets": build_prompt_target_manifest(source_prompts),
            "evalsets": build_evalset_manifests(train_path, optimizer_dev_path, val_path),
            "paths": {
                "train_evalset": str(train_path),
                "optimizer_dev_evalset": str(optimizer_dev_path),
                "validation_evalset": str(val_path),
                "final_validation_evalset": str(val_path),
                "optimizer_config": str(runtime_optimizer_path),
                "optimizer_source_config": str(optimizer_path),
                "evaluation_metrics": str(metrics_path),
                "online_eval_metrics": str(metrics_path),
                "system_prompt": str(system_path),
                "router_prompt": str(router_path),
            },
        },
        command=command,
        extra={
            **_optimizer_extra(result),
            "online_preflight": preflight,
            "online_duration": {
                "optimization_seconds": round(optimization_finished - started, 6),
                "baseline_revalidation_seconds": round(baseline_finished - optimization_finished, 6),
                "candidate_revalidation_seconds": round(candidate_duration_seconds, 6),
                "gate_elapsed_seconds": round(gate_elapsed_seconds, 6),
            },
            "optimization_rounds": optimization_rounds,
        },
    )
    write_report(run_dir, report)
    return run_dir


def _format_delta_number(value: Any, *, signed: bool = False) -> str:
    parsed = _finite_float(value)
    if parsed is None:
        return "n/a"
    return f"{parsed:+.2f}" if signed else f"{parsed:.2f}"


def render_markdown(report: dict[str, Any]) -> str:
    baseline_train = report["baseline"]["train"]["score"]
    baseline_optimizer_dev = report["baseline"]["optimizer_dev"]["score"]
    baseline_val = report["baseline"]["validation"]["score"]
    winner_id = report["gate_decision"]["winner"]
    winner = next((candidate for candidate in report["candidates"] if candidate["id"] == winner_id), None)
    winner_score = winner["validation"]["score"] if winner else baseline_val

    lines = [
        "# Optimization Report",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Mode: `{report['mode']}`",
        f"- Seed: `{report['seed']}`",
        f"- Baseline train score: `{baseline_train:.4f}`",
        f"- Baseline optimizer_dev score: `{baseline_optimizer_dev:.4f}`",
        f"- Baseline validation score: `{baseline_val:.4f}`",
        f"- Winner: `{winner_id or 'none'}`",
        f"- Winner validation score: `{winner_score:.4f}`",
        f"- Gate accepted: `{str(report['gate_decision']['accepted']).lower()}`",
        f"- Cost: `{report['cost'].get('estimated_total')}` `{report['cost'].get('currency')}`",
        f"- Duration seconds: `{report['duration_seconds']:.4f}`",
        "",
        "## Gate Reasons",
        "",
    ]
    for reason in report["gate_decision"]["reasons"]:
        lines.append(f"- {reason}")

    lines.extend(["", "## Candidate Summary", ""])
    for candidate in report["candidates"]:
        lines.append(
            "- `{id}` train `{train:.4f}` optimizer_dev `{optimizer_dev:.4f}` "
            "final_validation `{val:.4f}` accepted `{accepted}`".format(
                id=candidate["id"],
                train=candidate["train"]["score"],
                optimizer_dev=candidate["optimizer_dev"]["score"],
                val=candidate["validation"]["score"],
                accepted=str(candidate["gate"]["accepted"]).lower(),
            )
        )

    for candidate in sorted(report["candidates"], key=lambda item: str(item["id"])):
        lines.extend(["", f"## Validation Case Delta: `{candidate['id']}`", ""])
        for item in candidate["case_deltas"]:
            lines.append(
                "- `{case_id}`: `{baseline_score}` -> `{candidate_score}` "
                "delta `{delta}` change_type `{change_type}`".format(
                    case_id=item["case_id"],
                    baseline_score=_format_delta_number(item.get("baseline_score")),
                    candidate_score=_format_delta_number(item.get("candidate_score")),
                    delta=_format_delta_number(item.get("delta"), signed=True),
                    change_type=item["change_type"],
                )
            )

    lines.extend(["", "## Failure Attribution", ""])
    attribution = report["failure_attribution"]
    for name, count in attribution["taxonomy_counts"].items():
        lines.append(f"- `{name}`: `{count}`")

    lines.extend(["", "## Artifacts", ""])
    for name, path in sorted(report["artifacts"].items()):
        if path:
            lines.append(f"- `{name}`: `{path}`")

    return "\n".join(lines) + "\n"


def write_report(run_dir: Path, report: dict[str, Any]) -> None:
    validate_report_schema(report)
    json_path = _resolved_run_descendant(
        run_dir,
        "optimization_report.json",
        label="JSON report artifact",
    )
    markdown_path = _resolved_run_descendant(
        run_dir,
        "optimization_report.md",
        label="Markdown report artifact",
    )
    write_json(json_path, report)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


async def amain(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "trace", "online"), default="fake")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--train-evalset", type=Path, default=TRAIN_PATH)
    parser.add_argument("--optimizer-dev-evalset", type=Path, default=OPTIMIZER_DEV_PATH)
    parser.add_argument("--val-evalset", type=Path, default=VAL_PATH)
    parser.add_argument("--optimizer-config", type=Path, default=OPTIMIZER_CONFIG_PATH)
    parser.add_argument("--fixture-outputs", type=Path, default=None)
    parser.add_argument("--gate-config", type=Path, default=None)
    parser.add_argument("--system-prompt", type=Path, default=SYSTEM_PROMPT_PATH)
    parser.add_argument("--router-prompt", type=Path, default=ROUTER_PROMPT_PATH)
    args = parser.parse_args(argv)
    command_args = sys.argv[1:] if argv is None else argv
    command = " ".join([sys.executable, str(Path(__file__).resolve()), *command_args])

    if args.mode in {"fake", "trace"}:
        run_dir = await run_fake_or_trace(
            mode=args.mode,
            seed=args.seed,
            output_dir=args.output_dir,
            run_id=args.run_id,
            train_evalset=args.train_evalset,
            optimizer_dev_evalset=args.optimizer_dev_evalset,
            val_evalset=args.val_evalset,
            optimizer_config=args.optimizer_config,
            fixture_outputs=args.fixture_outputs,
            gate_config_path=args.gate_config,
            system_prompt=args.system_prompt,
            router_prompt=args.router_prompt,
            command=command,
        )
    else:
        run_dir = await run_online(
            seed=args.seed,
            output_dir=args.output_dir,
            run_id=args.run_id,
            train_evalset=args.train_evalset,
            optimizer_dev_evalset=args.optimizer_dev_evalset,
            val_evalset=args.val_evalset,
            optimizer_config=args.optimizer_config,
            gate_config_path=args.gate_config,
            system_prompt=args.system_prompt,
            router_prompt=args.router_prompt,
            command=command,
        )
    print(run_dir)
    return run_dir


def main(argv: list[str] | None = None) -> Path:
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    main()
