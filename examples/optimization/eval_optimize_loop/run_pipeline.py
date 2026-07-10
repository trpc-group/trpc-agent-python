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
import subprocess
import sys
import time
import uuid
import warnings
from collections import Counter
from collections.abc import Awaitable
from collections.abc import Callable
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


TRAIN_PATH = HERE / "train.evalset.json"
OPTIMIZER_DEV_PATH = HERE / "optimizer_dev.evalset.json"
VAL_PATH = HERE / "val.evalset.json"
FIXTURE_PATH = HERE / "fixtures" / "fake_outputs.json"
OPTIMIZER_CONFIG_PATH = HERE / "optimizer.json"
REPORT_SCHEMA_PATH = HERE / "optimization_report.schema.json"
PROMPT_DIR = HERE / "agent" / "prompts"
SYSTEM_PROMPT_PATH = PROMPT_DIR / "system.md"
ROUTER_PROMPT_PATH = PROMPT_DIR / "router.md"
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
KNOWN_ONLINE_WARNING_FILTERS = (
    "SSEDecoder._aiter_chunks close RuntimeWarning",
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
            "criterion": {
                "final_response": {
                    "json": {
                        "match": "exact"
                    }
                }
            },
        },
        {
            "metric_name": OFFLINE_RUBRIC_METRIC,
            "threshold": 1.0,
            "criterion": {
                "offline_rubric": {
                    "checks": [
                        "valid_json_object",
                        "route_present",
                        "tool_object_present"
                    ]
                }
            },
        }
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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def validate_report_schema(report: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator

    schema = load_json(REPORT_SCHEMA_PATH)
    Draft202012Validator(schema).validate(report)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        "known_warning_filters": list(KNOWN_ONLINE_WARNING_FILTERS),
    }


def install_known_online_warning_filters() -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"coroutine method 'aclose' of 'SSEDecoder\._aiter_chunks' was never awaited",
        category=RuntimeWarning,
    )


def resolve_path(path: Path | None, default: Path) -> Path:
    return (path or default).expanduser().resolve()


def optimizer_metric_names(config_path: Path) -> list[str]:
    evaluate = load_json(config_path).get("evaluate") or {}
    metrics = evaluate.get("metrics") or []
    names = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        name = metric.get("metric_name") or metric.get("metricName")
        if name:
            names.append(str(name))
    return names


def optimizer_required_metrics(config_path: Path) -> tuple[list[str], str]:
    payload = load_json(config_path)
    required = ((payload.get("optimize") or {}).get("stop") or {}).get("required_metrics")
    if required is None:
        return [], "optimizer_config"
    if required == "all":
        return optimizer_metric_names(config_path), "optimizer_config"
    if isinstance(required, str):
        return [required], "optimizer_config"
    return [str(name) for name in required], "optimizer_config"


def online_preflight() -> dict[str, bool]:
    return {name: bool(os.getenv(name)) for name in ONLINE_ENV_VARS}


def format_online_preflight(preflight: dict[str, bool]) -> str:
    parts = [
        f"{name}={'present' if preflight.get(name) else 'missing'}"
        for name in ONLINE_ENV_VARS
    ]
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
        return "\n".join(str(part.get("text", "") or "") for part in parts).strip()
    parts = getattr(content, "parts", None) or []
    return "\n".join(str(getattr(part, "text", "") or "") for part in parts).strip()


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
        path_payload = load_json(path)
        config.update(path_payload)
        if "required_metrics" in path_payload:
            required_source = "gate_config"
    if overrides:
        config.update(overrides)
        if "required_metrics" in overrides:
            required_source = "override"
    if optimizer_config is not None and required_source == "default":
        required, required_source = optimizer_required_metrics(optimizer_config)
        config["required_metrics"] = required
    elif optimizer_config is not None and config.get("required_metrics") == "all":
        config["required_metrics"] = optimizer_metric_names(optimizer_config)
    if config.get("required_metrics") is None:
        config["required_metrics"] = []
    if isinstance(config.get("required_metrics"), str) and config["required_metrics"] != "all":
        config["required_metrics"] = [config["required_metrics"]]
    config["required_metrics_source"] = required_source
    return config


def validate_inputs(train_evalset: Path, optimizer_dev_evalset: Path, val_evalset: Path) -> None:
    resolved = {
        "train": train_evalset.resolve(),
        "optimizer_dev": optimizer_dev_evalset.resolve(),
        "final_validation": val_evalset.resolve(),
    }
    if len(set(resolved.values())) != len(resolved):
        raise ValueError("train, optimizer_dev, and final validation evalsets must be physically separate files")
    for path in (train_evalset, optimizer_dev_evalset, val_evalset):
        if not path.is_file():
            raise FileNotFoundError(path)


def make_run_dir(output_dir: Path | None, run_id: str) -> Path:
    base = output_dir or DEFAULT_RUNS_DIR
    base = base.expanduser()
    if not base.is_absolute():
        base = (Path.cwd() / base).resolve()
    else:
        base = base.resolve()
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def offline_metrics_path(run_dir: Path) -> Path:
    path = run_dir / "offline_metrics.json"
    write_json(path, OFFLINE_METRICS_CONFIG)
    return path


def online_metrics_path(run_dir: Path, optimizer_config: Path) -> Path:
    path = run_dir / "online_eval_metrics.json"
    write_json(path, load_json(optimizer_config)["evaluate"])
    return path


def read_source_prompts(system_prompt: Path, router_prompt: Path) -> dict[str, tuple[Path, str]]:
    return {
        "system_prompt": (system_prompt, system_prompt.read_text(encoding="utf-8")),
        "router_prompt": (router_prompt, router_prompt.read_text(encoding="utf-8")),
    }


def offline_candidate_prompts(
    source_prompts: dict[str, tuple[Path, str]],
    candidate_id: str,
    summary: str,
) -> dict[str, str]:
    prompts = {name: text for name, (_, text) in source_prompts.items()}
    if candidate_id != "baseline":
        prompts["router_prompt"] = (
            prompts["router_prompt"].rstrip()
            + "\n\n"
            + f"Offline candidate patch ({candidate_id}): {summary}\n"
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
    prompt_dir = run_dir / "prompts" / candidate_id
    prompt_dir.mkdir(parents=True, exist_ok=True)
    audit: list[dict[str, Any]] = []
    patch_lines = [f"candidate: {candidate_id}", f"summary: {summary}", ""]
    for name, (source_path, source_text) in source_prompts.items():
        candidate_text = candidate_prompts.get(name, source_text)
        candidate_path = prompt_dir / f"{name}.md"
        candidate_path.write_text(candidate_text, encoding="utf-8")
        diff_text = prompt_diff(source_text, candidate_text, name)
        patch_lines.extend([f"## {name}", diff_text, ""])
        audit.append({
            "name": name,
            "source_path": str(source_path),
            "candidate_path": str(candidate_path),
            "sha256": sha256_text(candidate_text),
            "source_written": source_written,
            "summary": summary,
            "diff": diff_text,
        })
    patch_path = prompt_dir / "prompt_patch.diff"
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
                "actual route "
                f"{actual.get('route')!r} did not match expected route {expected.get('route')!r}"
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
                "actual tool "
                f"{actual_tool.get('name')!r} did not match expected tool {expected_tool.get('name')!r}"
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
    metric_scores: dict[str, list[float]] = {}
    metric_thresholds: dict[str, float] = {}

    for case in evalset_payload["eval_cases"]:
        eval_id = case["eval_id"]
        runs = set_result.eval_results_by_eval_id.get(eval_id, [])
        if not runs:
            metrics: dict[str, dict[str, Any]] = {}
            attribution = attribute_failure_case(
                actual_text="",
                expected_text=case_expected_text(case),
                error_message="AgentEvaluator returned no run for case",
                metrics=metrics,
            )
            case_results.append({
                "case_id": eval_id,
                "tags": case_tags(case),
                "user": case_user_text(case),
                "score": 0.0,
                "passed": False,
                "metrics": metrics,
                "actual_text": "",
                "root_cause": attribution["root_cause"],
                "reasons": attribution["reasons"],
            })
            continue

        run_scores: list[float] = []
        run_passed = True
        merged_metrics: dict[str, dict[str, Any]] = {}
        actual_text, expected_text = _extract_actual_expected(runs[0], case_by_id[eval_id])
        error_message = None
        for run in runs:
            run_passed = run_passed and _is_passed_status(run.final_eval_status)
            if run.error_message and error_message is None:
                error_message = run.error_message
            for metric in run.overall_eval_metric_results:
                score = metric.score
                metric_passed = _is_passed_status(metric.eval_status)
                details = getattr(metric, "details", None)
                reason = getattr(details, "reason", None) if details is not None else None
                threshold = float(metric.threshold)
                metric_thresholds[metric.metric_name] = threshold
                if score is not None:
                    metric_scores.setdefault(metric.metric_name, []).append(float(score))
                merged_metrics[metric.metric_name] = {
                    "score": None if score is None else float(score),
                    "threshold": threshold,
                    "status": _status_name(metric.eval_status),
                    "passed": metric_passed,
                    "reason": reason,
                }
                if metric.metric_name == PRIMARY_METRIC and score is not None:
                    run_scores.append(float(score))

        if not run_scores:
            run_scores = [
                float(metric["score"])
                for metric in merged_metrics.values()
                if metric.get("score") is not None
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
        case_results.append({
            "case_id": eval_id,
            "tags": case_tags(case),
            "user": case_user_text(case),
            "score": round(score_value, 6),
            "passed": run_passed,
            "metrics": merged_metrics,
            "actual_text": actual_text,
            "root_cause": attribution["root_cause"],
            "reasons": attribution["reasons"],
        })

    total = len(case_results)
    score = sum(item["score"] for item in case_results) / total if total else 0.0
    pass_rate = sum(1 for item in case_results if item["passed"]) / total if total else 0.0
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
    failed = [
        case for case in case_results
        if isinstance(case, dict) and case.get("passed") is not True
    ]
    counts = Counter({name: 0 for name in TAXONOMY})
    cases = []
    for case in failed:
        root = case.get("root_cause") or "runtime_error"
        if root not in TAXONOMY:
            root = "runtime_error"
        counts[root] += 1
        reasons = case.get("reasons") or ["no failure reason recorded"]
        cases.append({
            "case_id": str(case.get("case_id", "")),
            "root_cause": root,
            "score": _finite_float(case.get("score")),
            "reasons": reasons if isinstance(reasons, list) else [str(reasons)],
        })
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

    previous = EVALUATOR_REGISTRY.get_evaluator_class(
        EvalMetric(metric_name=OFFLINE_RUBRIC_METRIC, threshold=1.0)
    )

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
    query_to_output = {
        case_user_text(case): outputs.get(case["eval_id"], "")
        for case in evalset_payload["eval_cases"]
    }

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
            "parts": [{
                "text": outputs.get(case["eval_id"], "")
            }],
            "role": "model",
        }
        case["actual_conversation"] = [actual_invocation]
    path = run_dir / "evalsets" / f"{candidate_id}.{split}.trace.evalset.json"
    write_json(path, trace_payload)
    if candidate_id == "candidate_local_patch" and split == "validation":
        write_json(run_dir / "trace_evalset.json", trace_payload)
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
            None
            if baseline_score is None or candidate_score is None
            else round(candidate_score - baseline_score, 6)
        )
        if before is None:
            root_cause = "unexpected_candidate"
        elif after is None:
            root_cause = "missing_candidate"
        else:
            root_cause = after.get("root_cause", "")
        deltas.append({
            "case_id": case_id,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "delta": delta,
            "root_cause": root_cause,
            "reasons": [] if after is None else after.get("reasons", []),
        })
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
        if not isinstance(case, dict) or not str(case.get("case_id", "")).strip():
            issues.append(f"case_results[{position}] has no case_id")
            continue
        case_id = str(case["case_id"])
        if case_id in indexed:
            issues.append(f"duplicate case_id: {case_id}")
            continue
        indexed[case_id] = case
    return indexed, issues


def _normalized_gate_tags(case: dict[str, Any]) -> set[str]:
    tags = case.get("tags", [])
    if not isinstance(tags, (list, tuple, set)):
        return set()
    return {str(tag).lower() for tag in tags}


def apply_gate(
    *,
    candidate_id: str,
    baseline_val: dict[str, Any],
    candidate_val: dict[str, Any],
    gate_config: dict[str, Any],
    duration_seconds: float,
    cost_usd: float | None,
) -> dict[str, Any]:
    baseline_by_id, baseline_issues = _index_gate_cases(baseline_val)
    candidate_by_id, candidate_issues = _index_gate_cases(candidate_val)
    reasons: list[str] = []
    reasons.extend(baseline_issues)
    reasons.extend(candidate_issues)
    accepted = not reasons

    for label, cases in (("baseline", baseline_by_id), ("candidate", candidate_by_id)):
        for case_id, case in cases.items():
            if _finite_float(case.get("score")) is None:
                accepted = False
                reasons.append(f"{label} case {case_id} score must be a finite number")
            if not isinstance(case.get("passed"), bool):
                accepted = False
                reasons.append(f"{label} case {case_id} passed must be a boolean")
            if not isinstance(case.get("tags", []), (list, tuple, set)):
                accepted = False
                reasons.append(f"{label} case {case_id} tags must be an array")

    baseline_score = _finite_float(baseline_val.get("score"))
    candidate_score = _finite_float(candidate_val.get("score"))
    raw_validation_delta = (
        None
        if baseline_score is None or candidate_score is None
        else candidate_score - baseline_score
    )
    validation_delta = (
        raw_validation_delta
        if raw_validation_delta is not None and math.isfinite(raw_validation_delta)
        else None
    )
    if validation_delta is None:
        accepted = False
        reasons.append("baseline and candidate validation scores must be finite numbers")
    min_delta = _finite_float(gate_config.get("min_validation_delta", 0.0))
    if min_delta is None:
        accepted = False
        reasons.append("minimum validation delta must be a finite number")
    elif validation_delta is not None:
        if validation_delta <= 0 and min_delta == 0:
            accepted = False
            reasons.append("validation score did not improve over baseline")
        elif validation_delta <= min_delta:
            accepted = False
            reasons.append(
                "validation score improvement "
                f"{validation_delta:.4f} must be greater than required {min_delta:.4f}"
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
    new_hard_fail_ids = [
        case_id
        for case_id in common_case_ids
        if baseline_by_id[case_id].get("passed") and not candidate_by_id[case_id].get("passed")
    ]
    if new_hard_fail_ids and not gate_config.get("allow_new_hard_fails", False):
        accepted = False
        reasons.append("candidate introduced hard fail(s): " + ", ".join(new_hard_fail_ids))

    critical_regression_ids = [
        case_id
        for case_id in common_case_ids
        if "critical" in _normalized_gate_tags(candidate_by_id[case_id])
        and _finite_float(candidate_by_id[case_id].get("score")) is not None
        and _finite_float(baseline_by_id[case_id].get("score")) is not None
        and _finite_float(candidate_by_id[case_id].get("score"))
        < _finite_float(baseline_by_id[case_id].get("score"))
    ]
    if critical_regression_ids and not gate_config.get("allow_critical_regression", False):
        accepted = False
        reasons.append("candidate regressed critical case(s): " + ", ".join(critical_regression_ids))

    normalized_cost = None if cost_usd is None else _finite_float(cost_usd)
    if cost_usd is not None and normalized_cost is None:
        accepted = False
        reasons.append("run cost must be a finite number")

    max_cost = gate_config.get("max_cost_usd")
    normalized_max_cost = None if max_cost is None else _finite_float(max_cost)
    if max_cost is not None and normalized_max_cost is None:
        accepted = False
        reasons.append("cost budget must be a finite number")
    elif max_cost is not None and cost_usd is None:
        accepted = False
        reasons.append("cost budget could not be evaluated because run cost is unknown")
    elif max_cost is not None and normalized_cost is not None and normalized_cost > normalized_max_cost:
        accepted = False
        reasons.append(f"run exceeded cost budget: {normalized_cost:.4f} > {normalized_max_cost:.4f} USD")

    max_seconds = gate_config.get("max_duration_seconds")
    normalized_duration = _finite_float(duration_seconds)
    normalized_max_seconds = None if max_seconds is None else _finite_float(max_seconds)
    if normalized_duration is None:
        accepted = False
        reasons.append("run duration must be a finite number")
    elif max_seconds is not None and normalized_max_seconds is None:
        accepted = False
        reasons.append("duration budget must be a finite number")
    elif max_seconds is not None and normalized_duration > normalized_max_seconds:
        accepted = False
        reasons.append(f"run exceeded duration budget: {normalized_duration:.2f}s > {normalized_max_seconds:.2f}s")

    required = gate_config.get("required_metrics") or []
    candidate_metrics = candidate_val.get("metrics")
    if not isinstance(candidate_metrics, dict):
        candidate_metrics = {}
        accepted = False
        reasons.append("candidate metrics must be an object")
    if required == "all":
        required = sorted(candidate_metrics.keys())
    missing_or_failed = []
    for name in required:
        metric = candidate_metrics.get(name)
        if not isinstance(metric, dict) or metric.get("passed") is not True:
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
    cost_usd: float | None,
    prompt_artifacts: list[dict[str, Any]] | None = None,
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    gate = apply_gate(
        candidate_id=candidate_id,
        baseline_val=baseline_val,
        candidate_val=validation,
        gate_config=gate_config,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
    )
    return _json_safe({
        "id": candidate_id,
        "prompt_patch_summary": fixture.get("prompt_patch_summary", ""),
        "prompt_artifacts": prompt_artifacts or [],
        "train": _json_safe(train),
        "optimizer_dev": _json_safe(optimizer_dev),
        "final_validation": _json_safe(validation),
        "validation": _json_safe(validation),
        "delta": {
            "train_score": _score_delta(train.get("score"), baseline_train.get("score")),
            "optimizer_dev_score": _score_delta(
                optimizer_dev.get("score"), baseline_optimizer_dev.get("score")
            ),
            "validation_score": _score_delta(validation.get("score"), baseline_val.get("score")),
        },
        "case_deltas": build_case_deltas(baseline_val, validation),
        "gate": gate,
        "failure_attribution": attribution_for(validation),
        "artifacts": artifacts or {},
    })


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
            fixture_path=FIXTURE_PATH.resolve(),
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
    metrics_path = offline_metrics_path(run_dir)
    source_prompts = read_source_prompts(system_prompt, router_prompt)
    if mode == "trace":
        write_json(run_dir / "trace_metrics.json", OFFLINE_METRICS_CONFIG)

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
        duration_seconds = time.perf_counter() - started
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
                duration_seconds=duration_seconds,
                cost_usd=0.0,
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
        artifacts.update({
            "trace_evalset": str(run_dir / "trace_evalset.json"),
            "trace_metrics": str(run_dir / "trace_metrics.json"),
        })
    artifacts.update({
        "baseline_train_trace_evalset": baseline_train_artifacts.get("train_trace_evalset", ""),
        "baseline_optimizer_dev_trace_evalset": baseline_optimizer_dev_artifacts.get("optimizer_dev_trace_evalset", ""),
        "baseline_validation_trace_evalset": baseline_val_artifacts.get("validation_trace_evalset", ""),
        "baseline_prompt_dir": baseline_prompt_paths.get("prompt_dir", ""),
        "baseline_prompt_patch": baseline_prompt_paths.get("prompt_patch", ""),
    })

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
            "optimizer": {
                "estimated_cost": 0.0,
                "model_calls": 0,
                "reflection_lm_calls": 0,
                "judge_model_calls": 0,
                "token_usage": {
                    "prompt": 0,
                    "completion": 0,
                    "total": 0,
                },
            },
            "final_revalidation": {
                "estimated_cost": 0.0,
                "agent_calls": 0,
                "judge_model_calls": 0,
                "model_calls": 0,
            },
        },
        duration_seconds=time.perf_counter() - started,
        config_snapshot={
            "mode": mode,
            "seed": seed,
            "gate": gate_config,
            "paths": {
                "train_evalset": str(train_evalset),
                "optimizer_dev_evalset": str(optimizer_dev_evalset),
                "validation_evalset": str(val_evalset),
                "final_validation_evalset": str(val_evalset),
                "optimizer_config": str(optimizer_config),
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
        return final.strip()

    return call_agent


async def online_call_agent(query: str) -> str:
    return await make_online_call_agent(
        system_prompt=SYSTEM_PROMPT_PATH,
        router_prompt=ROUTER_PROMPT_PATH,
    )(query)


def _optimizer_fixture(result: Any) -> dict[str, str]:
    return {
        "prompt_patch_summary": "Best prompt returned by AgentOptimizer.optimize(update_source=False)."
    }


def _optimizer_extra(result: Any) -> dict[str, Any]:
    return {
        "online_result": {
            "status": result.status,
            "baseline_pass_rate": result.baseline_pass_rate,
            "best_pass_rate": result.best_pass_rate,
            "pass_rate_improvement": result.pass_rate_improvement,
            "stop_reason": result.stop_reason,
            "baseline_metric_breakdown": getattr(result, "baseline_metric_breakdown", {}),
            "best_metric_breakdown": getattr(result, "best_metric_breakdown", {}),
        }
    }


def _int_attr(obj: Any, name: str) -> int:
    try:
        return int(getattr(obj, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _token_usage_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"prompt": 0, "completion": 0, "total": 0}
    return {
        "prompt": int(value.get("prompt", 0) or 0),
        "completion": int(value.get("completion", 0) or 0),
        "total": int(value.get("total", 0) or 0),
    }


def _is_llm_metric(metric: dict[str, Any]) -> bool:
    name = str(metric.get("metric_name") or metric.get("metricName") or "")
    criterion = metric.get("criterion") or {}
    return name.startswith("llm_") or "llm_judge" in criterion or "llmJudge" in criterion


def final_revalidation_call_audit(
    summaries: list[dict[str, Any]],
    metrics_config: dict[str, Any],
) -> dict[str, int]:
    metrics = metrics_config.get("metrics") or []
    num_runs = int(metrics_config.get("num_runs", 1) or 1)
    case_runs = sum(len(summary.get("case_results", [])) for summary in summaries) * num_runs
    llm_metric_count = sum(1 for metric in metrics if isinstance(metric, dict) and _is_llm_metric(metric))
    judge_calls = case_runs * llm_metric_count
    return {
        "agent_calls": case_runs,
        "judge_model_calls": judge_calls,
        "model_calls": case_runs + judge_calls,
    }


def online_cost_audit(
    result: Any,
    *,
    final_revalidation_calls: dict[str, int],
) -> dict[str, Any]:
    reflection_calls = _int_attr(result, "total_reflection_lm_calls")
    judge_calls = _int_attr(result, "total_judge_model_calls")
    optimizer_calls = reflection_calls + judge_calls
    token_usage = _token_usage_dict(getattr(result, "total_token_usage", {}))
    raw_cost = getattr(result, "total_llm_cost", None)
    optimizer_cost: float | None = None
    if raw_cost is not None:
        try:
            parsed_cost = float(raw_cost)
            if parsed_cost != 0.0 or (optimizer_calls == 0 and token_usage["total"] == 0):
                optimizer_cost = parsed_cost
        except (TypeError, ValueError):
            optimizer_cost = None

    total_cost = optimizer_cost
    unknown_reasons: list[str] = []
    if optimizer_cost is None and (optimizer_calls > 0 or token_usage["total"] > 0):
        unknown_reasons.append("optimizer returned calls or tokens without a provider-priced cost")
        total_cost = None
    if final_revalidation_calls["model_calls"] > 0:
        unknown_reasons.append("final revalidation model calls are not provider-priced by AgentEvaluator")
        total_cost = None

    return {
        "currency": "USD",
        "estimated_total": total_cost,
        "cost_source": "unknown" if unknown_reasons else "optimizer_result",
        "unknown_cost_reason": "; ".join(unknown_reasons) if unknown_reasons else None,
        "model_calls": optimizer_calls + final_revalidation_calls["model_calls"],
        "token_usage": token_usage,
        "optimizer": {
            "estimated_cost": optimizer_cost,
            "model_calls": optimizer_calls,
            "reflection_lm_calls": reflection_calls,
            "judge_model_calls": judge_calls,
            "token_usage": token_usage,
        },
        "final_revalidation": {
            "estimated_cost": None,
            **final_revalidation_calls,
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
    fixture_path = resolve_path(fixture_outputs, FIXTURE_PATH)
    system_path = resolve_path(system_prompt, SYSTEM_PROMPT_PATH)
    router_path = resolve_path(router_prompt, ROUTER_PROMPT_PATH)
    validate_inputs(train_path, optimizer_dev_path, val_path)
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

    install_known_online_warning_filters()
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
    validate_inputs(train_path, optimizer_dev_path, val_path)
    gate = load_gate_config(gate_config_path, gate_config, optimizer_config=optimizer_path)
    source_prompts = read_source_prompts(system_path, router_path)

    online_dir = run_dir / "online"
    target = (
        TargetPrompt()
        .add_path("system_prompt", str(system_path))
        .add_path("router_prompt", str(router_path))
    )
    route_metric_state = _install_route_tool_args_metric()
    try:
        result = await AgentOptimizer.optimize(
            config_path=str(optimizer_path),
            call_agent=make_online_call_agent(system_prompt=system_path, router_prompt=router_path),
            target_prompt=target,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(optimizer_dev_path),
            output_dir=str(online_dir),
            update_source=False,
            verbose=0,
        )
    finally:
        _restore_route_tool_args_metric(route_metric_state)

    train_payload = load_json(train_path)
    optimizer_dev_payload = load_json(optimizer_dev_path)
    val_payload = load_json(val_path)
    metrics_path = online_metrics_path(run_dir, optimizer_path)
    baseline_call_agent = make_online_call_agent(system_prompt=system_path, router_prompt=router_path)
    best_call_agent = make_online_call_agent(
        system_prompt=system_path,
        router_prompt=router_path,
        prompt_texts=dict(result.best_prompts),
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

    duration_seconds = time.perf_counter() - started
    metrics_config = load_json(metrics_path)
    cost = online_cost_audit(
        result,
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
        ),
    )
    cost_usd = cost["estimated_total"]
    baseline_prompt_artifacts, baseline_prompt_paths = write_prompt_artifacts(
        run_dir=run_dir,
        candidate_id="baseline",
        source_prompts=source_prompts,
        candidate_prompts=dict(getattr(result, "baseline_prompts", {}) or {
            name: text for name, (_, text) in source_prompts.items()
        }),
        summary="Source prompts before AgentOptimizer.optimize.",
        source_written=False,
    )
    best_prompt_artifacts, best_prompt_paths = write_prompt_artifacts(
        run_dir=run_dir,
        candidate_id="optimizer_best",
        source_prompts=source_prompts,
        candidate_prompts=dict(result.best_prompts),
        summary="Best prompt returned by AgentOptimizer.optimize(update_source=False).",
        source_written=False,
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
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
        prompt_artifacts=best_prompt_artifacts,
        artifacts={
            "native_optimizer_dir": str(online_dir),
            "native_result_json": str(online_dir / "result.json"),
            "native_summary_txt": str(online_dir / "summary.txt"),
            "native_rounds_dir": str(online_dir / "rounds"),
            "native_baseline_prompts_dir": str(online_dir / "baseline_prompts"),
            "native_best_prompts_dir": str(online_dir / "best_prompts"),
            "native_best_prompts": str(online_dir / "best_prompts"),
            "native_config_snapshot_json": str(online_dir / "config.snapshot.json"),
            **best_prompt_paths,
        },
    )
    if result.status != "SUCCEEDED":
        candidate["gate"]["accepted"] = False
        candidate["gate"]["reasons"].append(f"native optimizer status was {result.status}")

    artifacts = common_artifacts(
        run_dir=run_dir,
        train_evalset=train_path,
        optimizer_dev_evalset=optimizer_dev_path,
        val_evalset=val_path,
        optimizer_config=optimizer_path,
        fixture_path=FIXTURE_PATH,
        metrics_path=metrics_path,
        system_prompt=system_path,
        router_prompt=router_path,
    )
    artifacts.update(candidate["artifacts"])
    artifacts.update({
        "online_eval_metrics": str(metrics_path),
        "baseline_prompt_dir": baseline_prompt_paths.get("prompt_dir", ""),
        "baseline_prompt_patch": baseline_prompt_paths.get("prompt_patch", ""),
    })
    report = build_top_level_report(
        mode="online",
        run_id=actual_run_id,
        run_dir=run_dir,
        seed=seed,
        baseline_fixture={
            "prompt_patch_summary": "Source prompts before AgentOptimizer.optimize."
        },
        baseline_train=baseline_train,
        baseline_optimizer_dev=baseline_optimizer_dev,
        baseline_val=baseline_val,
        baseline_prompt_artifacts=baseline_prompt_artifacts,
        candidates=[candidate],
        gate_config=gate,
        artifacts=artifacts,
        cost=cost,
        duration_seconds=duration_seconds,
        config_snapshot={
            "mode": "online",
            "seed": seed,
            "gate": gate,
            "paths": {
                "train_evalset": str(train_path),
                "optimizer_dev_evalset": str(optimizer_dev_path),
                "validation_evalset": str(val_path),
                "final_validation_evalset": str(val_path),
                "optimizer_config": str(optimizer_path),
                "online_eval_metrics": str(metrics_path),
                "system_prompt": str(system_path),
                "router_prompt": str(router_path),
            },
        },
        command=command,
        extra={**_optimizer_extra(result), "online_preflight": preflight},
    )
    write_report(run_dir, report)
    return run_dir


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

    if winner:
        lines.extend(["", "## Validation Case Delta", ""])
        for item in winner["case_deltas"]:
            lines.append(
                "- `{case_id}`: `{baseline_score:.2f}` -> `{candidate_score:.2f}` "
                "delta `{delta:+.2f}`".format(**item)
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
    write_json(run_dir / "optimization_report.json", report)
    (run_dir / "optimization_report.md").write_text(render_markdown(report), encoding="utf-8")


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
    parser.add_argument("--fixture-outputs", type=Path, default=FIXTURE_PATH)
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
