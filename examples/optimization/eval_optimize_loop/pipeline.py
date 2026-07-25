# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Auditable evaluation, optimization, and regression-gate pipeline."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed

if __package__:
    from .fake_model import FakePromptModel
else:
    from fake_model import FakePromptModel

JsonDict = dict[str, Any]
AsyncResponder = Callable[[str], Awaitable[str]]

FAILURE_FINAL_RESPONSE = "final_response_mismatch"
FAILURE_TOOL_CALL = "tool_call_error"
FAILURE_PARAMETER = "parameter_error"
FAILURE_LLM_RUBRIC = "llm_rubric_below_threshold"
FAILURE_KNOWLEDGE = "knowledge_recall_insufficient"
FAILURE_FORMAT = "format_noncompliance"
FAILURE_EXECUTION = "evaluation_execution_error"

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> JsonDict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def _write_json(path: Path, data: Any) -> None:
    _atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _default_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    for base in (_REPO_ROOT, Path.cwd().resolve()):
        try:
            return str(resolved.relative_to(base))
        except ValueError:
            continue
    return str(resolved)


def _safe_id(value: str) -> str:
    result = _SAFE_ID_RE.sub("_", value).strip("._")
    if not result:
        raise ValueError(f"Invalid empty identifier after normalization: {value!r}")
    return result


def _status_value(status: Any) -> str:
    name = getattr(status, "name", None)
    if isinstance(name, str):
        return name.lower()
    value = getattr(status, "value", status)
    if value == 1:
        return "passed"
    if value == 2:
        return "failed"
    if value == 3:
        return "not_evaluated"
    return str(value).lower()


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, dict):
        parts = content.get("parts") or []
    else:
        parts = getattr(content, "parts", []) or []
    chunks: list[str] = []
    for part in parts:
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


def _tool_calls(invocation: Any) -> list[JsonDict]:
    if invocation is None:
        return []
    if isinstance(invocation, dict):
        intermediate = (invocation.get("intermediate_data") or invocation.get("intermediateData"))
    else:
        intermediate = getattr(invocation, "intermediate_data", None)
    if isinstance(intermediate, dict):
        tool_uses = (intermediate.get("tool_uses") or intermediate.get("toolUses") or [])
    else:
        tool_uses = getattr(intermediate, "tool_uses", []) or []

    calls: list[JsonDict] = []
    for tool_use in tool_uses:
        if isinstance(tool_use, dict):
            name = tool_use.get("name", "")
            args = tool_use.get("args", {})
        else:
            name = getattr(tool_use, "name", "")
            args = getattr(tool_use, "args", {})
        calls.append({
            "name": name,
            "args": args,
        })
    return calls


def _case_context(dataset_path: Path) -> dict[str, JsonDict]:
    dataset = _read_json(dataset_path)
    contexts: dict[str, JsonDict] = {}
    for case in dataset.get("eval_cases", []):
        conversation = case.get("conversation") or []
        expected = _content_text(conversation[-1].get("final_response")) if conversation else ""
        query = _content_text(conversation[0].get("user_content")) if conversation else ""
        contexts[str(case["eval_id"])] = {
            "query": query,
            "expected": expected,
        }
    return contexts


def _case_ids(dataset_path: Path) -> set[str]:
    return set(_case_context(dataset_path))


def _looks_like_json(value: str) -> bool:
    try:
        json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


def classify_failure(
    *,
    metric_names: list[str],
    reasons: list[str],
    actual: str,
    expected: str,
    error_message: str = "",
) -> str:
    """Classify a failed case from metric, judge, output, and trace evidence."""
    metric_text = " ".join(metric_names).lower()
    evidence = " ".join([*reasons, actual, expected, error_message]).lower()

    if error_message:
        return FAILURE_EXECUTION
    if "parameter" in evidence or "argument" in evidence or "invalid args" in evidence:
        return FAILURE_PARAMETER
    if "tool_trajectory" in metric_text or "tool_error" in evidence or "tool call" in evidence:
        return FAILURE_TOOL_CALL
    if "knowledge_recall" in metric_text or "knowledge_miss" in evidence or "grounded source" in evidence:
        return FAILURE_KNOWLEDGE
    if "llm_rubric" in metric_text or "rubric" in evidence:
        return FAILURE_LLM_RUBRIC
    if (("format" in evidence or "schema" in evidence)
            or (_looks_like_json(expected) and not _looks_like_json(actual))):
        return FAILURE_FORMAT
    return FAILURE_FINAL_RESPONSE


def _summarize_evaluation(
    result: Any,
    *,
    dataset_name: str,
    dataset_path: Path,
) -> JsonDict:
    contexts = _case_context(dataset_path)
    cases: list[JsonDict] = []
    metric_totals: dict[str, list[float]] = {}

    for eval_set_result in result.results_by_eval_set_id.values():
        for eval_id, runs in eval_set_result.eval_results_by_eval_id.items():
            metric_scores: dict[str, list[float]] = {}
            metric_thresholds: dict[str, float] = {}
            metric_names: list[str] = []
            reasons: list[str] = []
            traces: list[JsonDict] = []
            run_statuses: list[str] = []
            error_messages: list[str] = []

            for run in runs:
                run_statuses.append(_status_value(run.final_eval_status))
                if run.error_message:
                    error_messages.append(str(run.error_message))
                for metric in run.overall_eval_metric_results:
                    metric_names.append(metric.metric_name)
                    if metric.score is not None:
                        score = float(metric.score)
                        metric_scores.setdefault(metric.metric_name, []).append(score)
                        metric_totals.setdefault(metric.metric_name, []).append(score)
                    metric_thresholds[metric.metric_name] = float(metric.threshold)
                    if metric.details and metric.details.reason:
                        reasons.append(str(metric.details.reason))
                for invocation in run.eval_metric_result_per_invocation:
                    actual_invocation = invocation.actual_invocation
                    expected_invocation = invocation.expected_invocation
                    actual_tool_calls = _tool_calls(actual_invocation)
                    expected_tool_calls = _tool_calls(expected_invocation)
                    if expected_tool_calls:
                        actual_names = [item["name"] for item in actual_tool_calls]
                        expected_names = [item["name"] for item in expected_tool_calls]
                        same_parameters = all(actual["args"] == expected["args"] for actual, expected in zip(
                            actual_tool_calls,
                            expected_tool_calls,
                        ))
                        if actual_names == expected_names and not same_parameters:
                            reasons.append("Tool call parameter mismatch between actual and expected trace.")
                        elif actual_names != expected_names:
                            reasons.append("Tool call sequence mismatch between actual and expected trace.")
                    traces.append({
                        "run_id":
                        run.run_id or 1,
                        "query":
                        _content_text(actual_invocation.user_content),
                        "actual_response":
                        _content_text(actual_invocation.final_response),
                        "expected_response":
                        _content_text(expected_invocation.final_response if expected_invocation else None),
                        "tool_calls":
                        actual_tool_calls,
                        "expected_tool_calls":
                        expected_tool_calls,
                    })

            averaged_metrics = {
                name: round(sum(scores) / len(scores), 6)
                for name, scores in sorted(metric_scores.items()) if scores
            }
            score = (sum(averaged_metrics.values()) / len(averaged_metrics) if averaged_metrics else 0.0)
            passed = bool(run_statuses) and all(status == "passed" for status in run_statuses)
            context = contexts.get(eval_id, {})
            actual = traces[-1]["actual_response"] if traces else ""
            expected = context.get("expected", "")
            reasons = list(dict.fromkeys(reasons))
            if not passed and not reasons:
                failed_metrics = [
                    name for name, metric_score in averaged_metrics.items()
                    if metric_score < metric_thresholds.get(name, 1.0)
                ]
                if failed_metrics:
                    reasons.append("Metrics below threshold: " + ", ".join(sorted(failed_metrics)))
                elif error_messages:
                    reasons.extend(error_messages)
                else:
                    reasons.append("Case did not satisfy the configured evaluation gate.")

            failure_type: Optional[str] = None
            if not passed:
                failure_type = classify_failure(
                    metric_names=metric_names,
                    reasons=reasons,
                    actual=actual,
                    expected=expected,
                    error_message="; ".join(error_messages),
                )

            cases.append({
                "case_id": eval_id,
                "passed": passed,
                "score": round(score, 6),
                "metric_scores": averaged_metrics,
                "metric_thresholds": metric_thresholds,
                "failure_type": failure_type,
                "failure_reasons": reasons,
                "key_trace": traces,
            })

    cases.sort(key=lambda item: item["case_id"])
    score = sum(case["score"] for case in cases) / len(cases) if cases else 0.0
    passed_count = sum(1 for case in cases if case["passed"])
    metric_summary = {
        name: round(sum(scores) / len(scores), 6)
        for name, scores in sorted(metric_totals.items()) if scores
    }
    return {
        "dataset": dataset_name,
        "evalset_path": _display_path(dataset_path),
        "case_count": len(cases),
        "passed_count": passed_count,
        "failed_count": len(cases) - passed_count,
        "pass_rate": round(passed_count / len(cases), 6) if cases else 0.0,
        "score": round(score, 6),
        "metric_scores": metric_summary,
        "cases": cases,
    }


async def _materialize_trace_dataset(
    source_path: Path,
    destination_path: Path,
    responder: AsyncResponder,
) -> Path:
    dataset = _read_json(source_path)
    trace_dataset = copy.deepcopy(dataset)
    trace_dataset["eval_set_id"] = f"{dataset['eval_set_id']}_trace"
    for case in trace_dataset.get("eval_cases", []):
        actual_conversation: list[JsonDict] = []
        for invocation in case.get("conversation") or []:
            actual = copy.deepcopy(invocation)
            query = _content_text(invocation.get("user_content"))
            response = await responder(query)
            actual["final_response"] = {
                "parts": [{
                    "text": response
                }],
                "role": "model",
            }
            actual_conversation.append(actual)
        case["evalMode"] = "trace"
        case["actualConversation"] = actual_conversation
    _write_json(destination_path, trace_dataset)
    return destination_path


async def run_evaluation(
    *,
    responder: AsyncResponder,
    dataset_name: str,
    dataset_path: Path,
    eval_config_path: Path,
    output_dir: Path,
    execution_mode: str,
) -> JsonDict:
    """Run AgentEvaluator and return a stable report-oriented summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluation_path = dataset_path
    call_agent: Optional[AsyncResponder] = responder

    if execution_mode == "trace":
        evaluation_path = await _materialize_trace_dataset(
            dataset_path,
            output_dir / f"{dataset_name}.trace.evalset.json",
            responder,
        )
        call_agent = None
    elif execution_mode != "call_agent":
        raise ValueError(f"Unsupported execution mode {execution_mode!r}; use 'trace' or 'call_agent'")

    kwargs: JsonDict = {
        "eval_dataset_file_path_or_dir": str(evaluation_path),
        "eval_metrics_file_path_or_dir": str(eval_config_path),
        "eval_result_output_dir": str(output_dir / "raw"),
        "print_detailed_results": False,
        "print_summary_report": False,
        "case_parallelism": 1,
        "case_eval_parallelism": 1,
    }
    if call_agent is not None:
        kwargs["call_agent"] = call_agent

    executer = AgentEvaluator.get_executer(**kwargs)
    try:
        await executer.evaluate()
    except _EvaluationCasesFailed:
        # AgentEvaluator intentionally raises when any case fails. The
        # structured result remains available and is the pipeline's input.
        pass
    result = executer.get_result()
    if result is None:
        raise RuntimeError(f"AgentEvaluator produced no result for {dataset_path}")

    raw_result = result.model_dump(mode="json", by_alias=True)
    _write_json(output_dir / "evaluate_result.json", raw_result)
    return _summarize_evaluation(
        result,
        dataset_name=dataset_name,
        dataset_path=dataset_path,
    )


def summarize_failures(evaluation: JsonDict) -> JsonDict:
    failed_cases = [case for case in evaluation["cases"] if not case["passed"]]
    counts = Counter(case["failure_type"] for case in failed_cases)
    return {
        "total_failures":
        len(failed_cases),
        "counts":
        dict(sorted(counts.items())),
        "cases": [{
            "case_id": case["case_id"],
            "failure_type": case["failure_type"],
            "reasons": case["failure_reasons"],
        } for case in failed_cases],
    }


def compare_evaluations(baseline: JsonDict, candidate: JsonDict) -> JsonDict:
    """Build aggregate, metric, and case-level candidate deltas."""
    baseline_cases = {case["case_id"]: case for case in baseline["cases"]}
    candidate_cases = {case["case_id"]: case for case in candidate["cases"]}
    comparisons: list[JsonDict] = []

    for case_id in sorted(set(baseline_cases) | set(candidate_cases)):
        base = baseline_cases.get(case_id, {
            "passed": False,
            "score": 0.0,
            "metric_scores": {},
        })
        current = candidate_cases.get(case_id, {
            "passed": False,
            "score": 0.0,
            "metric_scores": {},
        })
        score_delta = round(current["score"] - base["score"], 6)
        if not base["passed"] and current["passed"]:
            change = "new_pass"
        elif base["passed"] and not current["passed"]:
            change = "new_failure"
        elif score_delta > 0:
            change = "score_improved"
        elif score_delta < 0:
            change = "score_declined"
        else:
            change = "unchanged"
        metric_names = set(base["metric_scores"]) | set(current["metric_scores"])
        comparisons.append({
            "case_id": case_id,
            "baseline_passed": base["passed"],
            "candidate_passed": current["passed"],
            "baseline_score": base["score"],
            "candidate_score": current["score"],
            "score_delta": score_delta,
            "change": change,
            "metric_deltas": {
                name: round(
                    current["metric_scores"].get(name, 0.0) - base["metric_scores"].get(name, 0.0),
                    6,
                )
                for name in sorted(metric_names)
            },
        })

    metric_names = set(baseline["metric_scores"]) | set(candidate["metric_scores"])
    return {
        "score": round(candidate["score"] - baseline["score"], 6),
        "pass_rate": round(candidate["pass_rate"] - baseline["pass_rate"], 6),
        "metric_scores": {
            name: round(
                candidate["metric_scores"].get(name, 0.0) - baseline["metric_scores"].get(name, 0.0),
                6,
            )
            for name in sorted(metric_names)
        },
        "new_passes": [item["case_id"] for item in comparisons if item["change"] == "new_pass"],
        "new_failures": [item["case_id"] for item in comparisons if item["change"] == "new_failure"],
        "score_improvements": [item["case_id"] for item in comparisons if item["change"] == "score_improved"],
        "score_regressions": [item["case_id"] for item in comparisons if item["change"] == "score_declined"],
        "case_comparisons": comparisons,
    }


def evaluate_gate(
    *,
    gate_config: JsonDict,
    baseline_train: JsonDict,
    baseline_validation: JsonDict,
    candidate_train: JsonDict,
    candidate_validation: JsonDict,
    candidate_cost_usd: float,
) -> JsonDict:
    """Apply a fail-closed, independently testable acceptance policy."""
    train_delta = candidate_train["score"] - baseline_train["score"]
    validation_delta = candidate_validation["score"] - baseline_validation["score"]
    validation_comparison = compare_evaluations(
        baseline_validation,
        candidate_validation,
    )
    comparisons = {item["case_id"]: item for item in validation_comparison["case_comparisons"]}
    checks: list[JsonDict] = []

    def add_check(
        name: str,
        passed: bool,
        actual: Any,
        threshold: Any,
        reason: str,
    ) -> None:
        checks.append({
            "name": name,
            "passed": bool(passed),
            "actual": actual,
            "threshold": threshold,
            "reason": reason,
        })

    minimum_delta = float(gate_config.get("min_validation_score_delta", 0.0))
    add_check(
        "validation_score_delta",
        validation_delta >= minimum_delta,
        round(validation_delta, 6),
        f">={minimum_delta}",
        "Validation score must improve by the configured minimum.",
    )

    minimum_pass_rate_delta = float(gate_config.get("min_validation_pass_rate_delta", 0.0))
    pass_rate_delta = (candidate_validation["pass_rate"] - baseline_validation["pass_rate"])
    add_check(
        "validation_pass_rate_delta",
        pass_rate_delta >= minimum_pass_rate_delta,
        round(pass_rate_delta, 6),
        f">={minimum_pass_rate_delta}",
        "Validation pass rate must not miss the configured improvement.",
    )

    hard_case_ids = set(gate_config.get("hard_fail_case_ids", []))
    new_hard_failures = sorted(case_id for case_id in hard_case_ids
                               if case_id not in comparisons or comparisons[case_id]["change"] == "new_failure")
    add_check(
        "no_new_hard_fail",
        not new_hard_failures,
        new_hard_failures,
        "[]",
        "A hard-fail case that passed at baseline cannot become a failure.",
    )

    critical_ids = set(gate_config.get("critical_case_ids", []))
    critical_tolerance = float(gate_config.get("max_critical_case_score_drop", 0.0))
    critical_regressions = sorted(
        case_id for case_id in critical_ids if case_id not in comparisons
        or comparisons[case_id]["score_delta"] < -critical_tolerance or comparisons[case_id]["change"] == "new_failure")
    add_check(
        "critical_cases_do_not_regress",
        not critical_regressions,
        critical_regressions,
        f"score_drop<={critical_tolerance}",
        "Critical validation cases must remain stable.",
    )

    max_new_failures = int(gate_config.get("max_new_validation_failures", 0))
    new_failures = validation_comparison["new_failures"]
    add_check(
        "new_validation_failures",
        len(new_failures) <= max_new_failures,
        new_failures,
        f"count<={max_new_failures}",
        "Aggregate gains cannot hide too many newly failing cases.",
    )

    max_overfit_gap = float(gate_config.get("max_train_validation_gain_gap", 1.0))
    overfit_gap = train_delta - validation_delta
    add_check(
        "train_validation_gain_gap",
        overfit_gap <= max_overfit_gap,
        round(overfit_gap, 6),
        f"<={max_overfit_gap}",
        "A much larger train gain than validation gain indicates overfitting.",
    )

    max_cost_value = gate_config.get("max_candidate_cost_usd")
    try:
        max_cost = float(max_cost_value)
        valid_cost_budget = math.isfinite(max_cost) and max_cost >= 0.0
    except (TypeError, ValueError):
        max_cost = 0.0
        valid_cost_budget = False
    add_check(
        "candidate_cost",
        valid_cost_budget and candidate_cost_usd <= max_cost,
        round(candidate_cost_usd, 6),
        (f"<={max_cost}" if valid_cost_budget else "configured finite non-negative budget"),
        ("Candidate evaluation and optimization cost must stay within budget."
         if valid_cost_budget else "A finite non-negative candidate cost budget is required."),
    )

    accepted = all(check["passed"] for check in checks)
    failed_reasons = [
        f"{check['name']}: {check['reason']} actual={check['actual']}" for check in checks if not check["passed"]
    ]
    return {
        "accepted": accepted,
        "decision": "accept" if accepted else "reject",
        "reasons": (["All configured acceptance checks passed."] if accepted else failed_reasons),
        "checks": checks,
        "train_score_delta": round(train_delta, 6),
        "validation_score_delta": round(validation_delta, 6),
        "overfit_gap": round(overfit_gap, 6),
    }


def _validate_inputs(
    *,
    config: JsonDict,
    train_path: Path,
    validation_path: Path,
    prompt_path: Path,
) -> None:
    for path in (train_path, validation_path, prompt_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if train_path.resolve() == validation_path.resolve():
        raise ValueError("Training and validation evalsets must be different files")
    overlap = _case_ids(train_path) & _case_ids(validation_path)
    if overlap:
        raise ValueError("Training and validation case IDs must be disjoint: " + ", ".join(sorted(overlap)))
    if not isinstance(config.get("evaluate"), dict) or not config["evaluate"]:
        raise ValueError("optimizer.json must contain a non-empty evaluate object")
    pipeline_config = config.get("pipeline")
    if not isinstance(pipeline_config, dict):
        raise ValueError("optimizer.json must contain a pipeline object")
    if not isinstance(pipeline_config.get("gate"), dict):
        raise ValueError("optimizer.json pipeline.gate must be an object")
    execution_mode = pipeline_config.get("execution_mode", "trace")
    if execution_mode not in {"trace", "call_agent"}:
        raise ValueError(f"Unsupported execution mode {execution_mode!r}; use 'trace' or 'call_agent'")
    backend = pipeline_config.get("optimizer_backend", "fake")
    if backend == "fake":
        rounds = pipeline_config.get("fake_optimizer", {}).get("rounds", [])
        if not rounds:
            raise ValueError("Fake optimizer requires at least one configured round")
        round_ids = [str(item.get("id", "")) for item in rounds]
        if any(not round_id for round_id in round_ids):
            raise ValueError("Each fake optimizer round requires a non-empty id")
        if len(round_ids) != len(set(round_ids)):
            raise ValueError("Fake optimizer round IDs must be unique")
    elif backend == "agent_optimizer":
        if not isinstance(config.get("optimize"), dict) or not config["optimize"]:
            raise ValueError("agent_optimizer backend requires a non-empty optimize object")
    else:
        raise ValueError(f"Unsupported optimizer backend {backend!r}; use fake or agent_optimizer")


def _fake_candidates(config: JsonDict, baseline_prompt: str) -> list[JsonDict]:
    candidates: list[JsonDict] = []
    rounds = config["pipeline"]["fake_optimizer"]["rounds"]
    for index, round_config in enumerate(rounds, start=1):
        rules = [str(rule) for rule in round_config.get("append_rules", [])]
        rule_block = "\n".join(f"[RULE:{rule}]" for rule in rules)
        prompt = "\n".join([
            baseline_prompt.rstrip(),
            "",
            f"## Optimization round {index}: {round_config['id']}",
            rule_block,
            "",
        ])
        candidates.append({
            "id": str(round_config["id"]),
            "prompts": {
                "system_prompt": prompt
            },
            "optimizer_metadata": {
                "backend": "fake",
                "diagnosis": str(round_config.get("diagnosis", "")),
                "appended_rules": rules,
                "seed": config["pipeline"].get("seed", 42),
            },
            "optimization_cost_usd": float(round_config.get("optimization_cost_usd", 0.0)),
        })
    return candidates


async def _agent_optimizer_candidate(
    *,
    config: JsonDict,
    responder: AsyncResponder,
    prompt_path: Path,
    train_path: Path,
    validation_path: Path,
    output_dir: Path,
) -> list[JsonDict]:
    from trpc_agent_sdk.evaluation import AgentOptimizer
    from trpc_agent_sdk.evaluation import TargetPrompt

    target = TargetPrompt().add_path("system_prompt", str(prompt_path))
    agent_optimizer_config_path = output_dir / "agent_optimizer_config.json"
    _write_json(
        agent_optimizer_config_path,
        {
            "evaluate": config["evaluate"],
            "optimize": config["optimize"],
        },
    )
    result = await AgentOptimizer.optimize(
        config_path=str(agent_optimizer_config_path),
        call_agent=responder,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(validation_path),
        output_dir=str(output_dir / "agent_optimizer"),
        update_source=False,
        verbose=int(config["pipeline"].get("optimizer_verbose", 1)),
    )
    serialized = result.model_dump(mode="json", by_alias=True)
    _write_json(output_dir / "agent_optimizer_result.json", serialized)
    return [{
        "id": "agent_optimizer_best",
        "prompts": dict(result.best_prompts),
        "optimizer_metadata": {
            "backend": "agent_optimizer",
            "status": result.status,
            "finish_reason": result.finish_reason,
            "total_rounds": result.total_rounds,
            "reflection_lm_calls": result.total_reflection_lm_calls,
            "token_usage": result.total_token_usage,
        },
        "optimization_cost_usd": float(config["pipeline"].get("agent_optimizer_cost_usd", 0.0)),
    }]


def _select_candidate(rounds: list[JsonDict]) -> JsonDict:
    accepted = [item for item in rounds if item["gate_decision"]["accepted"]]
    pool = accepted or rounds
    return max(
        pool,
        key=lambda item: (
            item["evaluation"]["validation"]["score"],
            item["evaluation"]["validation"]["pass_rate"],
            item["evaluation"]["train"]["score"],
            -item["cost"]["total_usd"],
        ),
    )


def _markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown_report(report: JsonDict) -> str:
    baseline = report["baseline"]
    candidate = report["candidate"]
    gate = report["gate_decision"]
    lines = [
        "# Evaluation + Optimization Report",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Decision: **{gate['decision'].upper()}**",
        f"- Candidate: `{candidate['id']}`",
        f"- Execution mode: `{report['audit']['execution_mode']}`",
        "",
        "## Score Summary",
        "",
        "| Dataset | Baseline score | Candidate score | Delta | Baseline pass rate | Candidate pass rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("train", "validation"):
        before = baseline[name]
        after = candidate["evaluation"][name]
        lines.append(f"| {name} | {before['score']:.4f} | {after['score']:.4f} | "
                     f"{after['score'] - before['score']:+.4f} | "
                     f"{before['pass_rate']:.2%} | {after['pass_rate']:.2%} |")

    lines.extend([
        "",
        "## Gate Decision",
        "",
        "| Check | Result | Actual | Threshold |",
        "| --- | --- | --- | --- |",
    ])
    for check in gate["checks"]:
        lines.append(f"| {_markdown_escape(check['name'])} | "
                     f"{'PASS' if check['passed'] else 'FAIL'} | "
                     f"{_markdown_escape(check['actual'])} | "
                     f"{_markdown_escape(check['threshold'])} |")
    lines.extend(["", "Decision reasons:"])
    lines.extend(f"- {_markdown_escape(reason)}" for reason in gate["reasons"])

    lines.extend([
        "",
        "## Validation Case Delta",
        "",
        "| Case | Baseline | Candidate | Score delta | Change |",
        "| --- | --- | --- | ---: | --- |",
    ])
    for item in report["delta"]["validation"]["case_comparisons"]:
        lines.append(f"| `{item['case_id']}` | "
                     f"{'pass' if item['baseline_passed'] else 'fail'} | "
                     f"{'pass' if item['candidate_passed'] else 'fail'} | "
                     f"{item['score_delta']:+.4f} | `{item['change']}` |")

    lines.extend([
        "",
        "## Failure Attribution",
        "",
        "| Stage | Failure type | Count |",
        "| --- | --- | ---: |",
    ])
    for stage, summary in report["failure_attribution"].items():
        if not summary["counts"]:
            lines.append(f"| {stage} | none | 0 |")
        for failure_type, count in summary["counts"].items():
            lines.append(f"| {stage} | `{failure_type}` | {count} |")

    lines.extend([
        "",
        "## Optimization Rounds",
        "",
        "| Round | Candidate | Train score | Validation score | Gate | Cost (USD) |",
        "| ---: | --- | ---: | ---: | --- | ---: |",
    ])
    for item in report["rounds"]:
        lines.append(f"| {item['round']} | `{item['id']}` | "
                     f"{item['evaluation']['train']['score']:.4f} | "
                     f"{item['evaluation']['validation']['score']:.4f} | "
                     f"{item['gate_decision']['decision']} | "
                     f"{item['cost']['total_usd']:.4f} |")

    lines.extend([
        "",
        "## Audit",
        "",
        f"- Random seed: `{report['audit']['random_seed']}`",
        f"- Prompt source updated: `{report['audit']['source_prompt_updated']}`",
        f"- Total model calls: `{report['cost']['model_calls']}`",
        f"- Total estimated cost: `${report['cost']['total_usd']:.4f}`",
        f"- Duration: `{report['audit']['duration_seconds']:.4f}s`",
        f"- Config SHA-256: `{report['audit']['input_sha256']['optimizer_config']}`",
        "",
    ])
    return "\n".join(lines)


async def run_pipeline(
    *,
    config_path: Path,
    train_path: Path,
    validation_path: Path,
    prompt_path: Path,
    output_dir: Path,
    run_id: Optional[str] = None,
    update_source: bool = False,
) -> JsonDict:
    """Execute baseline, optimization rounds, validation gates, and audit."""
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    run_id = run_id or _default_run_id()
    config_path = config_path.resolve()
    train_path = train_path.resolve()
    validation_path = validation_path.resolve()
    prompt_path = prompt_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    config = _read_json(config_path)
    _validate_inputs(
        config=config,
        train_path=train_path,
        validation_path=validation_path,
        prompt_path=prompt_path,
    )
    pipeline_config = config["pipeline"]
    execution_mode = str(pipeline_config.get("execution_mode", "trace"))
    cost_per_call = float(pipeline_config.get("fake_model_cost_per_call_usd", 0.0))
    baseline_prompt = prompt_path.read_text(encoding="utf-8")
    fake_model = FakePromptModel(prompt_path)

    async def responder(query: str) -> str:
        return await fake_model.respond(query)

    audit_dir = output_dir / "audit"
    eval_config_path = audit_dir / "evaluation_config.json"
    _write_json(eval_config_path, config["evaluate"])
    _write_json(audit_dir / "resolved_optimizer_config.json", config)

    baseline_calls_before = fake_model.call_count
    baseline_train = await run_evaluation(
        responder=responder,
        dataset_name="train",
        dataset_path=train_path,
        eval_config_path=eval_config_path,
        output_dir=output_dir / "evaluations" / "baseline" / "train",
        execution_mode=execution_mode,
    )
    baseline_validation = await run_evaluation(
        responder=responder,
        dataset_name="validation",
        dataset_path=validation_path,
        eval_config_path=eval_config_path,
        output_dir=output_dir / "evaluations" / "baseline" / "validation",
        execution_mode=execution_mode,
    )
    baseline_calls = fake_model.call_count - baseline_calls_before
    baseline_cost = round(baseline_calls * cost_per_call, 6)

    backend = str(pipeline_config.get("optimizer_backend", "fake"))
    rounds: list[JsonDict] = []
    try:
        optimizer_calls_before = fake_model.call_count
        if backend == "fake":
            candidate_specs = _fake_candidates(config, baseline_prompt)
        else:
            candidate_specs = await _agent_optimizer_candidate(
                config=config,
                responder=responder,
                prompt_path=prompt_path,
                train_path=train_path,
                validation_path=validation_path,
                output_dir=output_dir / "optimizer",
            )
        optimizer_model_calls = fake_model.call_count - optimizer_calls_before
        if candidate_specs:
            candidate_specs[0]["optimization_model_calls"] = optimizer_model_calls

        for index, candidate_spec in enumerate(candidate_specs, start=1):
            candidate_id = _safe_id(candidate_spec["id"])
            prompt = candidate_spec["prompts"]["system_prompt"]
            candidate_dir = output_dir / "candidates" / f"{index:02d}_{candidate_id}"
            prompt_artifact = candidate_dir / "system_prompt.md"
            _atomic_write_text(prompt_artifact, prompt)
            _atomic_write_text(prompt_path, prompt)

            calls_before = fake_model.call_count
            candidate_train = await run_evaluation(
                responder=responder,
                dataset_name="train",
                dataset_path=train_path,
                eval_config_path=eval_config_path,
                output_dir=output_dir / "evaluations" / f"{index:02d}_{candidate_id}" / "train",
                execution_mode=execution_mode,
            )
            candidate_validation = await run_evaluation(
                responder=responder,
                dataset_name="validation",
                dataset_path=validation_path,
                eval_config_path=eval_config_path,
                output_dir=output_dir / "evaluations" / f"{index:02d}_{candidate_id}" / "validation",
                execution_mode=execution_mode,
            )
            evaluation_model_calls = fake_model.call_count - calls_before
            evaluation_cost = evaluation_model_calls * cost_per_call
            optimization_model_calls = int(candidate_spec.get("optimization_model_calls", 0))
            model_calls = evaluation_model_calls + optimization_model_calls
            total_cost = round(
                evaluation_cost + candidate_spec["optimization_cost_usd"],
                6,
            )
            gate = evaluate_gate(
                gate_config=pipeline_config["gate"],
                baseline_train=baseline_train,
                baseline_validation=baseline_validation,
                candidate_train=candidate_train,
                candidate_validation=candidate_validation,
                candidate_cost_usd=total_cost,
            )
            rounds.append({
                "round": index,
                "id": candidate_spec["id"],
                "prompts": candidate_spec["prompts"],
                "prompt_artifacts": {
                    "system_prompt": _display_path(prompt_artifact),
                    "sha256": _text_sha256(prompt),
                },
                "optimizer_metadata": candidate_spec["optimizer_metadata"],
                "evaluation": {
                    "train": candidate_train,
                    "validation": candidate_validation,
                },
                "delta": {
                    "train": compare_evaluations(baseline_train, candidate_train),
                    "validation": compare_evaluations(
                        baseline_validation,
                        candidate_validation,
                    ),
                },
                "gate_decision": gate,
                "failure_attribution": {
                    "train": summarize_failures(candidate_train),
                    "validation": summarize_failures(candidate_validation),
                },
                "cost": {
                    "model_calls": model_calls,
                    "evaluation_model_calls": evaluation_model_calls,
                    "optimization_model_calls": optimization_model_calls,
                    "evaluation_usd": round(evaluation_cost, 6),
                    "optimization_usd": round(
                        candidate_spec["optimization_cost_usd"],
                        6,
                    ),
                    "total_usd": total_cost,
                },
            })
            _atomic_write_text(prompt_path, baseline_prompt)
    finally:
        _atomic_write_text(prompt_path, baseline_prompt)

    selected = _select_candidate(rounds)
    source_prompt_updated = bool(update_source and selected["gate_decision"]["accepted"])
    if source_prompt_updated:
        _atomic_write_text(
            prompt_path,
            selected["prompts"]["system_prompt"],
        )

    duration = round(time.monotonic() - started_monotonic, 6)
    total_model_calls = baseline_calls + sum(item["cost"]["model_calls"] for item in rounds)
    total_cost = round(
        baseline_cost + sum(item["cost"]["total_usd"] for item in rounds),
        6,
    )
    report: JsonDict = {
        "schema_version": "v1",
        "run_id": run_id,
        "baseline": {
            "prompts": {
                "system_prompt": baseline_prompt
            },
            "train": baseline_train,
            "validation": baseline_validation,
        },
        "candidate": {
            "id": selected["id"],
            "prompts": selected["prompts"],
            "prompt_artifacts": selected["prompt_artifacts"],
            "evaluation": selected["evaluation"],
            "optimizer_metadata": selected["optimizer_metadata"],
        },
        "delta": selected["delta"],
        "gate_decision": selected["gate_decision"],
        "failure_attribution": {
            "baseline_train": summarize_failures(baseline_train),
            "baseline_validation": summarize_failures(baseline_validation),
            "candidate_train": selected["failure_attribution"]["train"],
            "candidate_validation": selected["failure_attribution"]["validation"],
        },
        "rounds": rounds,
        "cost": {
            "model_calls": total_model_calls,
            "baseline_usd": baseline_cost,
            "total_usd": total_cost,
        },
        "audit": {
            "started_at": started_at,
            "finished_at": _utc_now(),
            "duration_seconds": duration,
            "random_seed": pipeline_config.get("seed", 42),
            "optimizer_backend": backend,
            "execution_mode": execution_mode,
            "judge_mode": "fake_deterministic",
            "source_prompt_updated": source_prompt_updated,
            "input_paths": {
                "optimizer_config": _display_path(config_path),
                "train_evalset": _display_path(train_path),
                "validation_evalset": _display_path(validation_path),
                "prompt_source": _display_path(prompt_path),
            },
            "input_sha256": {
                "optimizer_config": _file_sha256(config_path),
                "train_evalset": _file_sha256(train_path),
                "validation_evalset": _file_sha256(validation_path),
                "prompt_source_baseline": _text_sha256(baseline_prompt),
            },
        },
    }
    _write_json(output_dir / "optimization_report.json", report)
    _atomic_write_text(
        output_dir / "optimization_report.md",
        render_markdown_report(report),
    )
    return report


def run_pipeline_sync(**kwargs: Any) -> JsonDict:
    """Synchronous convenience wrapper for scripts and notebooks."""
    return asyncio.run(run_pipeline(**kwargs))
