# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Reproducible Evaluation + Optimization closed-loop example.

The default backend is deterministic and offline:
1. Convert reference evalsets into trace-mode evalsets for baseline/candidate.
2. Score both stages with AgentEvaluator.
3. Attribute failures, compare per-case deltas, apply a configurable gate.
4. Persist optimization_report.json and optimization_report.md.

This keeps the public example runnable without an API key while preserving the
same data flow that a real prompt optimizer must satisfy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trpc_agent_sdk.evaluation import AgentEvaluator  # noqa: E402
from trpc_agent_sdk.evaluation import AgentOptimizer  # noqa: E402
from trpc_agent_sdk.evaluation import EvalConfig  # noqa: E402
from trpc_agent_sdk.evaluation import EvalMetric  # noqa: E402
from trpc_agent_sdk.evaluation import EvalSet  # noqa: E402
from trpc_agent_sdk.evaluation import EvalStatus  # noqa: E402
from trpc_agent_sdk.evaluation import EvaluationResult  # noqa: E402
from trpc_agent_sdk.evaluation import Evaluator  # noqa: E402
from trpc_agent_sdk.evaluation import EVALUATOR_REGISTRY  # noqa: E402
from trpc_agent_sdk.evaluation import IntermediateData  # noqa: E402
from trpc_agent_sdk.evaluation import Invocation  # noqa: E402
from trpc_agent_sdk.evaluation import PerInvocationResult  # noqa: E402
from trpc_agent_sdk.evaluation import TargetPrompt  # noqa: E402
from trpc_agent_sdk.types import Content  # noqa: E402
from trpc_agent_sdk.types import FunctionCall  # noqa: E402
from trpc_agent_sdk.types import Part  # noqa: E402

DEFAULT_CONFIG = HERE / "optimizer.json"
DEFAULT_TRAIN = HERE / "train.evalset.json"
DEFAULT_VAL = HERE / "val.evalset.json"

MetricMap = dict[str, dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _without_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key in {"api_key", "apiKey"} else _without_secret_fields(child))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_without_secret_fields(item) for item in value]
    return value


def _display_path(path: Path, base_dir: Path) -> str:
    try:
        rel = os.path.relpath(path, base_dir)
    except ValueError:
        return str(path)
    if not rel.startswith(".."):
        return rel.replace("\\", "/")
    return str(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _content(text: str, role: str = "model") -> Content:
    return Content(role=role, parts=[Part.from_text(text=text)])


def _content_text(content: Optional[Content]) -> str:
    if not content or not content.parts:
        return ""
    return "\n".join(part.text or "" for part in content.parts if part.text is not None)


def _invocation_user_text(invocation: Invocation) -> str:
    return _content_text(invocation.user_content)


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> FunctionCall:
    return FunctionCall(id=call_id, name=name, args=args)


def _tools_to_dicts(tools: list[FunctionCall]) -> list[dict[str, Any]]:
    return [{"id": tool.id or "", "name": tool.name or "", "args": dict(tool.args or {})} for tool in tools]


def _case_reference_invocation(case: Any) -> Invocation:
    if not case.conversation:
        raise ValueError(f"eval case {case.eval_id!r} has no reference conversation")
    return case.conversation[0]


def _case_expected_tools(case: Any) -> list[FunctionCall]:
    invocation = _case_reference_invocation(case)
    if not invocation.intermediate_data or not isinstance(invocation.intermediate_data, IntermediateData):
        return []
    return list(invocation.intermediate_data.tool_uses)


def _case_expected_text(case: Any) -> str:
    return _content_text(_case_reference_invocation(case).final_response)


def _loads_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (TypeError, json.JSONDecodeError):
        return False


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


class FakeRubricResponseEvaluator(Evaluator):
    """Local deterministic rubric metric used by this example only."""

    requires_reference = True

    def __init__(self, threshold: Optional[float] = None, eval_metric: Optional[EvalMetric] = None):
        if threshold is not None and eval_metric is not None:
            raise ValueError("pass either threshold or eval_metric, not both")
        self._threshold = threshold if threshold is not None else (eval_metric.threshold if eval_metric else 1.0)

    def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        if expected_invocations is None:
            raise ValueError("expected_invocations is required for fake_llm_rubric_response")

        per_invocation: list[PerInvocationResult] = []
        for actual, expected in zip(actual_invocations, expected_invocations):
            actual_text = _content_text(actual.final_response)
            expected_text = _content_text(expected.final_response)
            user_text = _invocation_user_text(expected).lower()
            exact = actual_text == expected_text
            expected_json = _loads_json(expected_text)
            actual_json = _loads_json(actual_text)

            rubric_scores = [
                {
                    "id": "reference_match",
                    "score": 1.0 if exact else 0.0,
                    "reason": "final response exactly matches reference" if exact else "final response differs",
                },
                {
                    "id": "json_format",
                    "score": 1.0 if (not expected_json or actual_json) else 0.0,
                    "reason": "valid JSON when expected" if (not expected_json or actual_json) else "invalid JSON",
                },
                {
                    "id": "knowledge_grounding",
                    "score": 0.0 if ("internal" in user_text and not exact) else 1.0,
                    "reason": "internal knowledge answer is grounded" if exact else "no unsupported internal answer",
                },
            ]
            score = sum(float(item["score"]) for item in rubric_scores) / len(rubric_scores)
            status = EvalStatus.PASSED if score >= self._threshold else EvalStatus.FAILED
            failed_ids = [item["id"] for item in rubric_scores if float(item["score"]) < 1.0]
            reason = "all fake rubrics passed" if not failed_ids else "failed fake rubrics: " + ", ".join(failed_ids)
            per_invocation.append(
                PerInvocationResult(
                    actual_invocation=actual,
                    expected_invocation=expected,
                    score=score,
                    eval_status=status,
                    reason=reason,
                    rubric_scores=rubric_scores,
                ))

        if not per_invocation:
            return EvaluationResult()
        overall = sum(float(result.score or 0.0) for result in per_invocation) / len(per_invocation)
        return EvaluationResult(
            overall_score=overall,
            overall_eval_status=EvalStatus.PASSED if overall >= self._threshold else EvalStatus.FAILED,
            per_invocation_results=per_invocation,
        )


def _register_example_evaluators() -> None:
    if "fake_llm_rubric_response" not in EVALUATOR_REGISTRY.list_registered():
        EVALUATOR_REGISTRY.register("fake_llm_rubric_response", FakeRubricResponseEvaluator)


class PromptPolicy:
    """Tiny prompt interpreter for the deterministic trace-mode agent."""

    def __init__(self, prompts: dict[str, str]) -> None:
        prompt_text = self._positive_prompt_text("\n".join(prompts.values()).lower())
        self.strict_json = self._mentions(prompt_text, ["strict_json_output", "strict json", "json exactly", "compact json"])
        self.strict_tool_args = self._mentions(
            prompt_text,
            ["strict_tool_arguments", "strict tool", "copy required tool", "copy required tool names and arguments exactly"],
        )
        self.training_bias = self._mentions(
            prompt_text,
            [
                "training_pattern_bias",
                "training pattern bias",
                "training-set lookup",
                "training set lookup",
                "training-set lookup shortcut",
                "training-vip",
                "train-only",
            ],
        )
        self.no_overfit = self._mentions(
            prompt_text,
            [
                "do not overfit",
                "avoid overfitting",
                "do not use training-only",
                "do not use train-only",
                "preserve validation",
            ],
        )

    @staticmethod
    def _mentions(text: str, needles: list[str]) -> bool:
        return any(needle in text for needle in needles)

    @staticmethod
    def _positive_prompt_text(text: str) -> str:
        lines = []
        for line in text.splitlines():
            normalized = line.strip().lstrip("-*0123456789. ")
            if normalized.startswith(("no ", "missing ", "lacks ", "without ")):
                continue
            lines.append(line)
        return "\n".join(lines)


class DeterministicTraceModel:
    """Small fake model that creates baseline and overfit candidate traces."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.calls = 0
        self.cost = 0.0
        self.token_usage = {"prompt": 0, "completion": 0, "total": 0}

    def predict(self, *, split: str, stage: str, case: Any, prompts: dict[str, str]) -> Invocation:
        self.calls += 1
        expected = _case_reference_invocation(case)
        expected_text = _content_text(expected.final_response)
        expected_tools = _case_expected_tools(case)
        query = _invocation_user_text(expected)
        text, tools = self._prediction(
            split=split,
            stage=stage,
            case_id=case.eval_id,
            query=query,
            expected_text=expected_text,
            expected_tools=expected_tools,
            policy=PromptPolicy(prompts),
        )
        self._record_usage(query, text)
        return Invocation(
            invocation_id=expected.invocation_id,
            user_content=deepcopy(expected.user_content),
            final_response=_content(text),
            intermediate_data=IntermediateData(tool_uses=tools),
            creation_timestamp=time.time(),
        )

    def _prediction(
        self,
        *,
        split: str,
        stage: str,
        case_id: str,
        query: str,
        expected_text: str,
        expected_tools: list[FunctionCall],
        policy: PromptPolicy,
    ) -> tuple[str, list[FunctionCall]]:
        if stage == "baseline":
            return self._baseline(split, case_id, query, expected_text, expected_tools)
        return self._candidate(split, case_id, query, expected_text, expected_tools, policy=policy)

    def _baseline(
        self,
        split: str,
        case_id: str,
        query: str,
        expected_text: str,
        expected_tools: list[FunctionCall],
    ) -> tuple[str, list[FunctionCall]]:
        if case_id in {"train_format_json", "val_format_json"}:
            return ("The requested total is available, but not returned as JSON.", list(expected_tools))
        if case_id == "train_tool_args":
            wrong_tool = _tool_call("get_weather_risk", {"city": "Guangzhou", "date": "2026-07-06"}, "wrong-city")
            return (expected_text, [wrong_tool])
        if case_id == "train_knowledge_gap":
            return ("I do not have access to the internal launch code.", [])
        query_lower = query.lower()
        if self._looks_like_knowledge_gap(query_lower):
            return ("I do not have access to the required internal knowledge.", [])
        if split == "validation" and self._looks_like_regression_case(query_lower):
            return (expected_text, list(expected_tools))
        if self._looks_like_format_case(query_lower, expected_text):
            return ("The requested value is available, but not returned as JSON.", list(expected_tools))
        if self._looks_like_tool_arg_case(query_lower, expected_tools):
            return (expected_text, self._wrong_tool_args(expected_tools, "baseline"))
        return (expected_text, list(expected_tools))

    def _candidate(
        self,
        split: str,
        case_id: str,
        query: str,
        expected_text: str,
        expected_tools: list[FunctionCall],
        *,
        policy: PromptPolicy,
    ) -> tuple[str, list[FunctionCall]]:
        fixes_format = policy.strict_json
        fixes_tools = policy.strict_tool_args
        overfits_validation = policy.training_bias and not policy.no_overfit
        baseline_text, baseline_tools = self._baseline(split, case_id, query, expected_text, expected_tools)

        if split == "train" and case_id == "train_format_json" and fixes_format:
            return (expected_text, list(expected_tools))
        if split == "train" and case_id == "train_tool_args" and fixes_tools:
            return (expected_text, list(expected_tools))
        if case_id == "train_knowledge_gap":
            return (baseline_text, baseline_tools)
        if case_id == "val_format_json" and fixes_format:
            return (expected_text, list(expected_tools))
        if case_id == "val_critical_discount" and overfits_validation:
            wrong_tool = _tool_call("lookup_customer_discount", {"customer_id": "C7", "tier": "training-vip"},
                                    "overfit-discount")
            return ("{\"customer_id\":\"C7\",\"discount_percent\":15}", [wrong_tool])
        if case_id == "val_stable_refund" and overfits_validation:
            wrong_tool = _tool_call("lookup_refund_sla", {"ticket_id": "R55", "region": "train-only"}, "overfit-refund")
            return ("{\"ticket_id\":\"R55\",\"sla_hours\":24}", [wrong_tool])
        query_lower = query.lower()
        if self._looks_like_knowledge_gap(query_lower):
            return ("I do not have access to the required internal knowledge.", [])
        if split == "validation" and self._looks_like_regression_case(query_lower) and overfits_validation:
            return (self._overfit_response(expected_text), self._wrong_tool_args(expected_tools, "overfit"))
        if split == "train" and (
                (fixes_format and self._looks_like_format_case(query_lower, expected_text))
                or (fixes_tools and self._looks_like_tool_arg_case(query_lower, expected_tools))):
            return (expected_text, list(expected_tools))
        if fixes_format and self._looks_like_format_case(query_lower, expected_text):
            return (expected_text, list(expected_tools))
        return (baseline_text, baseline_tools)

    def _record_usage(self, prompt: str, completion: str) -> None:
        prompt_tokens = _estimate_tokens(prompt)
        completion_tokens = _estimate_tokens(completion)
        self.token_usage["prompt"] += prompt_tokens
        self.token_usage["completion"] += completion_tokens
        self.token_usage["total"] += prompt_tokens + completion_tokens

    @staticmethod
    def _looks_like_knowledge_gap(query_lower: str) -> bool:
        return any(word in query_lower for word in ("internal", "private", "secret", "launch code", "knowledge gap"))

    @staticmethod
    def _looks_like_format_case(query_lower: str, expected_text: str) -> bool:
        return ("json" in query_lower or _loads_json(expected_text)) and _loads_json(expected_text)

    @staticmethod
    def _looks_like_tool_arg_case(query_lower: str, expected_tools: list[FunctionCall]) -> bool:
        if not expected_tools:
            return False
        return any(word in query_lower for word in ("tool", "argument", "args", "weather", "risk", "lookup"))

    @staticmethod
    def _looks_like_regression_case(query_lower: str) -> bool:
        return any(word in query_lower for word in ("critical", "discount", "vip", "refund", "sla", "stable"))

    @staticmethod
    def _wrong_tool_args(expected_tools: list[FunctionCall], marker: str) -> list[FunctionCall]:
        if not expected_tools:
            return []
        wrong_tools = []
        for index, tool in enumerate(expected_tools):
            args = dict(tool.args or {})
            if index == 0:
                if args:
                    first_key = sorted(args)[0]
                    args[first_key] = f"{args[first_key]}-{marker}"
                else:
                    args["overfit_marker"] = marker
            wrong_tools.append(_tool_call(tool.name or "unknown_tool", args, f"{marker}-{index}"))
        return wrong_tools

    @staticmethod
    def _overfit_response(expected_text: str) -> str:
        try:
            parsed = json.loads(expected_text)
        except json.JSONDecodeError:
            return f"{expected_text} (overfit candidate)"
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if isinstance(value, bool):
                    parsed[key] = not value
                    break
                if isinstance(value, (int, float)):
                    parsed[key] = value + 1
                    break
                if isinstance(value, str):
                    parsed[key] = f"{value}-overfit"
                    break
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _make_trace_eval_set(
    reference: EvalSet,
    *,
    split: str,
    stage: str,
    model: DeterministicTraceModel,
    prompts: dict[str, str],
) -> EvalSet:
    cases = []
    for case in reference.eval_cases:
        cases.append(
            case.model_copy(
                deep=True,
                update={
                    "eval_mode": "trace",
                    "actual_conversation": [model.predict(split=split, stage=stage, case=case, prompts=prompts)],
                },
            ))
    return EvalSet(
        eval_set_id=f"{reference.eval_set_id}_{stage}_trace",
        app_name=reference.app_name,
        name=f"{reference.name or reference.eval_set_id} ({stage} trace)",
        description=f"Generated {stage} trace for {split}",
        eval_cases=cases,
    )


async def _evaluate_trace_set(trace_set: EvalSet, eval_config: EvalConfig) -> dict[str, Any]:
    failed_summary, details, result_lines, eval_results = await AgentEvaluator.evaluate_eval_set(
        trace_set,
        eval_config=eval_config,
        print_detailed_results=False,
    )
    return {
        "failed_summary": failed_summary,
        "details": details,
        "result_lines": result_lines,
        "eval_results_by_eval_id": eval_results,
    }


def _status_name(status: EvalStatus) -> str:
    return status.name.lower()


def _metric_summary(metric: Any) -> dict[str, Any]:
    return {
        "score": metric.score,
        "status": _status_name(metric.eval_status),
        "threshold": metric.threshold,
        "reason": metric.details.reason if metric.details else None,
        "rubric_scores": metric.details.rubric_scores if metric.details else None,
    }


def _case_metrics(result: Any) -> MetricMap:
    return {metric.metric_name: _metric_summary(metric) for metric in result.overall_eval_metric_results}


def _case_score(metrics: MetricMap) -> float:
    if not metrics:
        return 0.0
    return sum(float(metric["score"] or 0.0) for metric in metrics.values()) / len(metrics)


def _compare_tools(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> tuple[bool, bool]:
    name_mismatch = len(actual) != len(expected)
    args_mismatch = len(actual) != len(expected)
    for actual_tool, expected_tool in zip(actual, expected):
        if actual_tool["name"] != expected_tool["name"]:
            name_mismatch = True
        if actual_tool["args"] != expected_tool["args"]:
            args_mismatch = True
    return name_mismatch, args_mismatch


def _attribute_failures(result: Any) -> list[dict[str, Any]]:
    metrics = _case_metrics(result)
    first_invocation = result.eval_metric_result_per_invocation[0] if result.eval_metric_result_per_invocation else None
    if first_invocation is None:
        return [{"type": "not_evaluated", "reason": result.error_message or "no invocation was evaluated"}]

    actual_text = _content_text(first_invocation.actual_invocation.final_response)
    expected_text = _content_text(first_invocation.expected_invocation.final_response if first_invocation.expected_invocation else None)
    query = _invocation_user_text(first_invocation.expected_invocation) if first_invocation.expected_invocation else ""
    actual_tools = _tools_to_dicts(first_invocation.actual_invocation.intermediate_data.tool_uses
                                  if isinstance(first_invocation.actual_invocation.intermediate_data, IntermediateData)
                                  else [])
    expected_tools = _tools_to_dicts(first_invocation.expected_invocation.intermediate_data.tool_uses
                                    if first_invocation.expected_invocation
                                    and isinstance(first_invocation.expected_invocation.intermediate_data, IntermediateData)
                                    else [])
    reasons: list[dict[str, Any]] = []

    final_metric = metrics.get("final_response_avg_score")
    if final_metric and final_metric["status"] == "failed":
        if "internal" in query.lower() or "knowledge" in query.lower():
            reasons.append({"type": "knowledge_recall_insufficient", "reason": "answer missed a required internal fact"})
        if _loads_json(expected_text) and not _loads_json(actual_text):
            reasons.append({"type": "format_noncompliance", "reason": "expected JSON but actual response is not valid JSON"})
        if not reasons:
            reasons.append({"type": "final_response_mismatch", "reason": "actual final response differs from reference"})

    tool_metric = metrics.get("tool_trajectory_avg_score")
    if tool_metric and tool_metric["status"] == "failed":
        if not actual_tools and expected_tools:
            reasons.append({"type": "tool_call_error", "reason": "expected tool call was missing"})
        else:
            name_mismatch, args_mismatch = _compare_tools(actual_tools, expected_tools)
            if name_mismatch:
                reasons.append({"type": "tool_call_error", "reason": "tool call name or count differs from reference"})
            if args_mismatch:
                reasons.append({"type": "tool_argument_error", "reason": "tool arguments differ from reference"})

    rubric_metric = metrics.get("fake_llm_rubric_response") or metrics.get("llm_rubric_response")
    if rubric_metric and rubric_metric["status"] == "failed":
        reasons.append({"type": "llm_rubric_not_met", "reason": rubric_metric["reason"] or "rubric score below threshold"})

    if result.final_eval_status == EvalStatus.FAILED and not reasons:
        reasons.append({"type": "unknown_failure", "reason": result.error_message or "case failed without metric reason"})
    return reasons


def _summarize_phase(*, split: str, stage: str, trace_set: EvalSet, evaluation: dict[str, Any]) -> dict[str, Any]:
    raw_results = evaluation["eval_results_by_eval_id"]
    cases = []
    metric_values: dict[str, list[float]] = defaultdict(list)
    failures_by_type: Counter[str] = Counter()
    pass_count = 0

    for eval_id in sorted(raw_results):
        result = raw_results[eval_id][0]
        metrics = _case_metrics(result)
        for name, metric in metrics.items():
            metric_values[name].append(float(metric["score"] or 0.0))
        failures = _attribute_failures(result)
        for failure in failures:
            failures_by_type[failure["type"]] += 1
        if result.final_eval_status == EvalStatus.PASSED:
            pass_count += 1

        first_invocation = result.eval_metric_result_per_invocation[0] if result.eval_metric_result_per_invocation else None
        actual_tools: list[dict[str, Any]] = []
        expected_tools: list[dict[str, Any]] = []
        actual_response = ""
        expected_response = ""
        query = ""
        if first_invocation is not None:
            actual = first_invocation.actual_invocation
            expected = first_invocation.expected_invocation
            actual_response = _content_text(actual.final_response)
            expected_response = _content_text(expected.final_response if expected else None)
            query = _invocation_user_text(expected) if expected else _invocation_user_text(actual)
            if isinstance(actual.intermediate_data, IntermediateData):
                actual_tools = _tools_to_dicts(actual.intermediate_data.tool_uses)
            if expected and isinstance(expected.intermediate_data, IntermediateData):
                expected_tools = _tools_to_dicts(expected.intermediate_data.tool_uses)

        cases.append({
            "eval_id": eval_id,
            "query": query,
            "status": _status_name(result.final_eval_status),
            "score": round(_case_score(metrics), 6),
            "metrics": metrics,
            "actual_response": actual_response,
            "expected_response": expected_response,
            "actual_tools": actual_tools,
            "expected_tools": expected_tools,
            "failure_reasons": failures,
            "trace": {
                "session_id": result.session_id,
                "run_id": result.run_id,
            },
        })

    total_cases = len(cases)
    metric_breakdown = {
        name: round(sum(values) / len(values), 6) if values else 0.0 for name, values in sorted(metric_values.items())
    }
    total_score = round(sum(case["score"] for case in cases) / total_cases, 6) if total_cases else 0.0
    return {
        "split": split,
        "stage": stage,
        "eval_set_id": trace_set.eval_set_id,
        "case_count": total_cases,
        "pass_count": pass_count,
        "pass_rate": round(pass_count / total_cases, 6) if total_cases else 0.0,
        "total_score": total_score,
        "metric_breakdown": metric_breakdown,
        "cases": cases,
        "failure_attribution": {
            "by_type": dict(sorted(failures_by_type.items())),
            "failed_case_count": sum(1 for case in cases if case["status"] == "failed"),
        },
    }


def _case_index(phase: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["eval_id"]: case for case in phase["cases"]}


def _build_delta(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_cases = _case_index(baseline)
    candidate_cases = _case_index(candidate)
    case_deltas = []
    new_passes = []
    new_failures = []
    improved = []
    regressed = []

    for eval_id in sorted(set(baseline_cases) | set(candidate_cases)):
        before = baseline_cases.get(eval_id)
        after = candidate_cases.get(eval_id)
        if before is None or after is None:
            continue
        score_delta = round(float(after["score"]) - float(before["score"]), 6)
        status_delta = f"{before['status']}->{after['status']}"
        if before["status"] != "passed" and after["status"] == "passed":
            new_passes.append(eval_id)
        if before["status"] == "passed" and after["status"] != "passed":
            new_failures.append(eval_id)
        if score_delta > 0:
            improved.append(eval_id)
        if score_delta < 0:
            regressed.append(eval_id)
        case_deltas.append({
            "eval_id": eval_id,
            "baseline_score": before["score"],
            "candidate_score": after["score"],
            "score_delta": score_delta,
            "status_delta": status_delta,
            "classification": (
                "new_pass" if eval_id in new_passes else
                "new_failure" if eval_id in new_failures else
                "score_improved" if score_delta > 0 else
                "score_regressed" if score_delta < 0 else "unchanged"
            ),
        })

    return {
        "baseline_total_score": baseline["total_score"],
        "candidate_total_score": candidate["total_score"],
        "total_score_delta": round(float(candidate["total_score"]) - float(baseline["total_score"]), 6),
        "baseline_pass_rate": baseline["pass_rate"],
        "candidate_pass_rate": candidate["pass_rate"],
        "pass_rate_delta": round(float(candidate["pass_rate"]) - float(baseline["pass_rate"]), 6),
        "new_passes": new_passes,
        "new_failures": new_failures,
        "score_improved": improved,
        "score_regressed": regressed,
        "case_deltas": case_deltas,
    }


def _apply_gate(delta: dict[str, Any], gate_config: dict[str, Any], *, total_cost: float) -> dict[str, Any]:
    checks = {}
    reasons = []

    min_improvement = float(gate_config.get("min_validation_score_improvement", 0.0))
    checks["validation_score_improvement"] = {
        "passed": delta["total_score_delta"] >= min_improvement,
        "observed": delta["total_score_delta"],
        "required": min_improvement,
    }
    if not checks["validation_score_improvement"]["passed"]:
        reasons.append(
            f"validation score delta {delta['total_score_delta']:+.4f} is below required {min_improvement:+.4f}")

    allow_new_hard_failures = bool(gate_config.get("allow_new_hard_failures", False))
    checks["no_new_hard_failures"] = {
        "passed": allow_new_hard_failures or not delta["new_failures"],
        "new_failures": delta["new_failures"],
        "allow_new_hard_failures": allow_new_hard_failures,
    }
    if not checks["no_new_hard_failures"]["passed"]:
        reasons.append("candidate introduced new hard failures: " + ", ".join(delta["new_failures"]))

    critical_ids = list(gate_config.get("critical_case_ids", []))
    critical_regressions = [case_id for case_id in critical_ids if case_id in delta["score_regressed"]]
    checks["critical_cases_not_degraded"] = {
        "passed": not critical_regressions,
        "critical_case_ids": critical_ids,
        "regressed": critical_regressions,
    }
    if critical_regressions:
        reasons.append("critical cases regressed: " + ", ".join(critical_regressions))

    max_cost = float(gate_config.get("max_cost", float("inf")))
    checks["cost_budget"] = {
        "passed": total_cost <= max_cost,
        "observed": total_cost,
        "budget": max_cost,
    }
    if total_cost > max_cost:
        reasons.append(f"run cost {total_cost:.4f} exceeded budget {max_cost:.4f}")

    accepted = all(check["passed"] for check in checks.values())
    if accepted:
        reasons.append("candidate passed every configured gate")
    return {
        "accepted": accepted,
        "decision": "accept" if accepted else "reject",
        "checks": checks,
        "reasons": reasons,
    }


def _build_target_prompt(config: dict[str, Any], base_dir: Path) -> TargetPrompt:
    target_prompt = TargetPrompt()
    for item in config["optimize"].get("target_prompts", []):
        path = (base_dir / item["path"]).resolve()
        target_prompt.add_path(item["name"], str(path))
    return target_prompt


def _fake_optimize_prompts(
    baseline_prompts: dict[str, str],
    config: dict[str, Any],
    *,
    input_failure_attribution: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    started = time.monotonic()
    patch_lines = list(config["optimize"].get("fake_model", {}).get("candidate_patch", []))
    candidate_prompts = dict(baseline_prompts)
    system = candidate_prompts.get("system_prompt", "")
    candidate_prompts["system_prompt"] = system.rstrip() + "\n\nOptimization candidate:\n- " + "\n- ".join(patch_lines) + "\n"
    round_record = {
        "round": 1,
        "backend": "fake",
        "optimized_field_names": ["system_prompt"],
        "input_failure_attribution": input_failure_attribution,
        "candidate_prompts": candidate_prompts,
        "candidate_prompt_preview": {
            "system_prompt": candidate_prompts["system_prompt"][-600:],
        },
        "accepted_by_optimizer": True,
        "acceptance_reason": "fake optimizer produced one deterministic candidate for downstream gate validation",
        "cost": 0.0,
        "duration_seconds": round(time.monotonic() - started, 6),
    }
    return candidate_prompts, [round_record], {
        "status": "SUCCEEDED",
        "algorithm": "deterministic_fake_patch",
        "total_reflection_lm_calls": 0,
        "total_token_usage": {
            "prompt": 0,
            "completion": 0,
            "total": 0,
        },
        "result_artifacts": {},
    }


def _deep_update(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_update(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _env_names_in(value: Any) -> set[str]:
    if isinstance(value, dict):
        found: set[str] = set()
        for child in value.values():
            found.update(_env_names_in(child))
        return found
    if isinstance(value, list):
        found = set()
        for child in value:
            found.update(_env_names_in(child))
        return found
    if not isinstance(value, str):
        return set()
    names: set[str] = set()
    for match in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)|%([A-Za-z_][A-Za-z0-9_]*)%", value):
        names.add(next(group for group in match.groups() if group))
    return names


def _response_only_eval_config(config: dict[str, Any]) -> dict[str, Any]:
    metrics = []
    for metric in config["evaluate"].get("metrics", []):
        name = metric.get("metric_name") or metric.get("metricName")
        if name in {"tool_trajectory_avg_score", "llm_rubric_knowledge_recall"}:
            continue
        metrics.append(deepcopy(metric))
    if not metrics:
        raise ValueError("agent_optimizer backend needs at least one response-only metric")
    return {
        "metrics": metrics,
        "num_runs": int(config["evaluate"].get("num_runs", 1)),
    }


def _build_agent_optimizer_config(config: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    optimize_cfg = config.get("optimize", {})
    agent_cfg = optimize_cfg.get("agent_optimizer", {})

    os.environ.setdefault("TRPC_AGENT_OPT_MODEL", str(agent_cfg.get("model_name", "deepseek-v4-pro")))
    os.environ.setdefault("TRPC_AGENT_OPT_BASE_URL", str(agent_cfg.get("base_url", "https://api.deepseek.com/v1")))

    default_algorithm = {
        "name": "gepa_reflective",
        "seed": int(optimize_cfg.get("seed", 0)),
        "reflection_lm": {
            "provider_name": "openai",
            "model_name": "${TRPC_AGENT_OPT_MODEL}",
            "base_url": "${TRPC_AGENT_OPT_BASE_URL}",
            "api_key": "${TRPC_AGENT_OPT_API_KEY}",
            "generation_config": {
                "max_tokens": 4096,
                "temperature": 0.4,
            },
        },
        "candidate_selection_strategy": "pareto",
        "module_selector": "round_robin",
        "frontier_type": "instance",
        "reflection_minibatch_size": 2,
        "skip_perfect_score": False,
        "use_merge": False,
        "max_metric_calls": 12,
        "max_candidate_proposals": int(optimize_cfg.get("max_rounds", 1)),
        "max_iterations_without_improvement": 2,
    }
    algorithm = _deep_update(default_algorithm, agent_cfg.get("algorithm", {}))
    evaluate = deepcopy(agent_cfg.get("evaluate") or _response_only_eval_config(config))
    metric_names = [metric.get("metric_name") or metric.get("metricName") for metric in evaluate.get("metrics", [])]
    return {
        "evaluate": evaluate,
        "optimize": {
            "eval_case_parallelism": int(agent_cfg.get("eval_case_parallelism", 1)),
            "stop": {
                "required_metrics": agent_cfg.get("required_metrics", [name for name in metric_names if name]),
            },
            "algorithm": algorithm,
        },
        "_artifact_output_dir": str(output_dir / str(agent_cfg.get("artifact_dir", "agent_optimizer_run"))),
    }


def _assert_required_optimizer_env(agent_optimizer_config: dict[str, Any]) -> None:
    required = _env_names_in(agent_optimizer_config["optimize"]["algorithm"].get("reflection_lm", {}))
    missing = sorted(name for name in required if not os.getenv(name))
    if missing:
        raise RuntimeError(
            "agent_optimizer backend requires environment variable(s): "
            + ", ".join(missing)
            + ". Do not put API keys in optimizer.json; export them before running.")


def _optimizer_round_to_report(record: Any) -> dict[str, Any]:
    payload = record.model_dump(mode="json", by_alias=True)
    payload["backend"] = "agent_optimizer"
    payload["accepted_by_optimizer"] = bool(payload.get("accepted", False))
    payload["cost"] = float(payload.get("round_llm_cost", 0.0) or 0.0)
    return payload


def _build_optimizer_call_agent(
    *,
    train_set: EvalSet,
    val_set: EvalSet,
    target_prompt: TargetPrompt,
    model: DeterministicTraceModel,
):
    case_by_query: dict[str, tuple[str, Any]] = {}
    for split, eval_set in (("train", train_set), ("validation", val_set)):
        for case in eval_set.eval_cases:
            case_by_query[_invocation_user_text(_case_reference_invocation(case))] = (split, case)

    async def call_agent(query: str) -> str:
        split, case = case_by_query[query]
        prompts = await target_prompt.read_all()
        invocation = model.predict(split=split, stage="candidate", case=case, prompts=prompts)
        return _content_text(invocation.final_response)

    return call_agent


def _stage_agent_optimizer_evalsets(*, artifact_dir: Path, train_path: Path, val_path: Path) -> tuple[str, str]:
    """Copy optimizer input evalsets under the cwd and return colon-safe relative paths."""
    data_dir = artifact_dir / "input_evalsets"
    data_dir.mkdir(parents=True, exist_ok=True)
    staged_train = data_dir / "train.evalset.json"
    staged_val = data_dir / "val.evalset.json"
    _write_text(staged_train, train_path.read_text(encoding="utf-8"))
    _write_text(staged_val, val_path.read_text(encoding="utf-8"))
    return (
        os.path.relpath(staged_train, Path.cwd()).replace("\\", "/"),
        os.path.relpath(staged_val, Path.cwd()).replace("\\", "/"),
    )


async def _agent_optimizer_prompts(
    *,
    baseline_prompts: dict[str, str],
    config: dict[str, Any],
    config_path: Path,
    train_path: Path,
    val_path: Path,
    train_set: EvalSet,
    val_set: EvalSet,
    output_dir: Path,
    target_prompt: TargetPrompt,
    model: DeterministicTraceModel,
    input_failure_attribution: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    started = time.monotonic()
    runtime_config = _build_agent_optimizer_config(config, output_dir=output_dir)
    _assert_required_optimizer_env(runtime_config)

    artifact_dir = Path(runtime_config.pop("_artifact_output_dir"))
    staged_train_path, staged_val_path = _stage_agent_optimizer_evalsets(
        artifact_dir=artifact_dir,
        train_path=train_path,
        val_path=val_path,
    )
    runtime_config_path = output_dir / "agent_optimizer.config.json"
    _write_json(runtime_config_path, runtime_config)

    result = await AgentOptimizer.optimize(
        config_path=str(runtime_config_path),
        call_agent=_build_optimizer_call_agent(
            train_set=train_set,
            val_set=val_set,
            target_prompt=target_prompt,
            model=model,
        ),
        target_prompt=target_prompt,
        train_dataset_path=staged_train_path,
        validation_dataset_path=staged_val_path,
        output_dir=str(artifact_dir),
        update_source=False,
        verbose=0,
    )
    optimizer_succeeded = result.status == "SUCCEEDED"
    candidate_prompts = dict(result.best_prompts if optimizer_succeeded and result.best_prompts else baseline_prompts)
    rounds = [_optimizer_round_to_report(record) for record in result.rounds]
    if not rounds:
        rounds = [{
            "round": 0,
            "backend": "agent_optimizer",
            "optimized_field_names": [],
            "input_failure_attribution": input_failure_attribution,
            "candidate_prompts": candidate_prompts,
            "accepted_by_optimizer": optimizer_succeeded and candidate_prompts != baseline_prompts,
            "acceptance_reason": result.error_message or result.finish_reason,
            "cost": float(result.total_llm_cost or 0.0),
            "duration_seconds": round(time.monotonic() - started, 6),
        }]
    summary = _without_secret_fields(result.model_dump(mode="json", by_alias=True))
    summary["candidate_source"] = "best_prompts" if optimizer_succeeded else "baseline_prompts_due_to_optimizer_failure"
    summary["input_failure_attribution"] = input_failure_attribution
    summary["config_path"] = _display_path(runtime_config_path, config_path.parent)
    summary["artifact_dir"] = _display_path(artifact_dir, config_path.parent)
    summary["staged_train_evalset"] = staged_train_path
    summary["staged_validation_evalset"] = staged_val_path
    return candidate_prompts, rounds, summary


def _save_trace_sets(output_dir: Path, traces: dict[str, EvalSet], *, base_dir: Path) -> dict[str, str]:
    trace_dir = output_dir / "trace_evalsets"
    trace_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, eval_set in traces.items():
        path = trace_dir / f"{name}.evalset.json"
        _write_text(path, eval_set.model_dump_json(indent=2, by_alias=True) + "\n")
        paths[name] = _display_path(path, base_dir)
    return paths


def _render_markdown(report: dict[str, Any]) -> str:
    gate = report["gate_decision"]
    train_delta = report["delta"]["train"]
    delta = report["delta"]["validation"]
    lines = [
        "# Evaluation + Optimization Report",
        "",
        f"- Decision: **{gate['decision'].upper()}**",
        f"- Baseline train score: `{train_delta['baseline_total_score']:.4f}`",
        f"- Candidate train score: `{train_delta['candidate_total_score']:.4f}`",
        f"- Train delta: `{train_delta['total_score_delta']:+.4f}`",
        f"- Baseline validation score: `{delta['baseline_total_score']:.4f}`",
        f"- Candidate validation score: `{delta['candidate_total_score']:.4f}`",
        f"- Validation delta: `{delta['total_score_delta']:+.4f}`",
        f"- New passes: `{', '.join(delta['new_passes']) or 'none'}`",
        f"- New failures: `{', '.join(delta['new_failures']) or 'none'}`",
        "",
        "## Gate Reasons",
        "",
    ]
    for reason in gate["reasons"]:
        lines.append(f"- {reason}")

    lines.extend([
        "",
        "## Train Case Delta",
        "",
        "| case | baseline | candidate | delta | classification |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for item in train_delta["case_deltas"]:
        lines.append(
            f"| `{item['eval_id']}` | {item['baseline_score']:.4f} | "
            f"{item['candidate_score']:.4f} | {item['score_delta']:+.4f} | {item['classification']} |")

    lines.extend([
        "",
        "## Validation Case Delta",
        "",
        "| case | baseline | candidate | delta | classification |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for item in delta["case_deltas"]:
        lines.append(
            f"| `{item['eval_id']}` | {item['baseline_score']:.4f} | "
            f"{item['candidate_score']:.4f} | {item['score_delta']:+.4f} | {item['classification']} |")

    lines.extend([
        "",
        "## Failure Attribution",
        "",
    ])
    for key, stats in report["failure_attribution"].items():
        by_type = stats["by_type"]
        rendered = ", ".join(f"{name}={count}" for name, count in by_type.items()) or "none"
        lines.append(f"- `{key}`: {rendered}")

    lines.extend([
        "",
        "## Audit",
        "",
        f"- Seed: `{report['audit']['seed']}`",
        f"- Backend: `{report['optimization']['backend']}`",
        f"- Duration seconds: `{report['audit']['duration_seconds']:.4f}`",
        f"- Total fake/model calls: `{report['optimization']['model_calls']}`",
        f"- Total cost: `{report['optimization']['total_cost']:.4f}`",
    ])
    return "\n".join(lines) + "\n"


async def run_pipeline(
    *,
    config_path: Path,
    train_path: Path,
    val_path: Path,
    output_dir: Path,
    backend_override: Optional[str] = None,
) -> dict[str, Any]:
    started_monotonic = time.monotonic()
    started_at = _utc_now()
    _register_example_evaluators()

    config = _read_json(config_path)
    if backend_override:
        config.setdefault("optimize", {})["backend"] = backend_override
    eval_config = EvalConfig.model_validate(config["evaluate"])
    train_set = EvalSet.model_validate_json(train_path.read_text(encoding="utf-8"))
    val_set = EvalSet.model_validate_json(val_path.read_text(encoding="utf-8"))

    seed = int(config.get("optimize", {}).get("seed", 0))
    model = DeterministicTraceModel(seed=seed)
    target_prompt = _build_target_prompt(config, config_path.parent)
    baseline_prompts = await target_prompt.read_all()

    baseline_trace_sets = {
        "baseline_train": _make_trace_eval_set(
            train_set,
            split="train",
            stage="baseline",
            model=model,
            prompts=baseline_prompts,
        ),
        "baseline_val": _make_trace_eval_set(
            val_set,
            split="validation",
            stage="baseline",
            model=model,
            prompts=baseline_prompts,
        ),
    }
    baseline_evaluations = {
        name: await _evaluate_trace_set(trace_set, eval_config) for name, trace_set in baseline_trace_sets.items()
    }

    phases = {
        "baseline_train": _summarize_phase(
            split="train",
            stage="baseline",
            trace_set=baseline_trace_sets["baseline_train"],
            evaluation=baseline_evaluations["baseline_train"],
        ),
        "baseline_validation": _summarize_phase(
            split="validation",
            stage="baseline",
            trace_set=baseline_trace_sets["baseline_val"],
            evaluation=baseline_evaluations["baseline_val"],
        ),
    }

    input_failure_attribution = {
        "train": phases["baseline_train"]["failure_attribution"],
        "validation": phases["baseline_validation"]["failure_attribution"],
    }
    backend = config["optimize"].get("backend", "fake")
    if backend == "fake":
        candidate_prompts, rounds, optimizer_summary = _fake_optimize_prompts(
            baseline_prompts,
            config,
            input_failure_attribution=input_failure_attribution,
        )
    elif backend == "agent_optimizer":
        candidate_prompts, rounds, optimizer_summary = await _agent_optimizer_prompts(
            baseline_prompts=baseline_prompts,
            config=config,
            config_path=config_path,
            train_path=train_path,
            val_path=val_path,
            train_set=train_set,
            val_set=val_set,
            output_dir=output_dir,
            target_prompt=target_prompt,
            model=model,
            input_failure_attribution=input_failure_attribution,
        )
    else:
        raise ValueError(f"unsupported optimize.backend={backend!r}; expected 'fake' or 'agent_optimizer'")

    candidate_trace_sets = {
        "candidate_train": _make_trace_eval_set(
            train_set,
            split="train",
            stage="candidate",
            model=model,
            prompts=candidate_prompts,
        ),
        "candidate_val": _make_trace_eval_set(
            val_set,
            split="validation",
            stage="candidate",
            model=model,
            prompts=candidate_prompts,
        ),
    }
    candidate_evaluations = {
        name: await _evaluate_trace_set(trace_set, eval_config) for name, trace_set in candidate_trace_sets.items()
    }
    phases.update({
        "candidate_train": _summarize_phase(
            split="train",
            stage="candidate",
            trace_set=candidate_trace_sets["candidate_train"],
            evaluation=candidate_evaluations["candidate_train"],
        ),
        "candidate_validation": _summarize_phase(
            split="validation",
            stage="candidate",
            trace_set=candidate_trace_sets["candidate_val"],
            evaluation=candidate_evaluations["candidate_val"],
        ),
    })
    trace_sets = {**baseline_trace_sets, **candidate_trace_sets}

    train_delta = _build_delta(phases["baseline_train"], phases["candidate_train"])
    val_delta = _build_delta(phases["baseline_validation"], phases["candidate_validation"])
    optimizer_cost = float(optimizer_summary.get("total_llm_cost", 0.0) or 0.0)
    if optimizer_cost <= 0.0:
        optimizer_cost = sum(float(round_record.get("cost", 0.0)) for round_record in rounds)
    optimizer_usage = optimizer_summary.get("total_token_usage", {}) if isinstance(optimizer_summary, dict) else {}
    total_token_usage = {
        "prompt": int(model.token_usage.get("prompt", 0)) + int(optimizer_usage.get("prompt", 0) or 0),
        "completion": int(model.token_usage.get("completion", 0)) + int(optimizer_usage.get("completion", 0) or 0),
        "total": int(model.token_usage.get("total", 0)) + int(optimizer_usage.get("total", 0) or 0),
    }
    total_cost = model.cost + optimizer_cost
    gate_decision = _apply_gate(val_delta, config.get("gate", {}), total_cost=total_cost)
    duration = time.monotonic() - started_monotonic

    output_dir.mkdir(parents=True, exist_ok=True)
    trace_paths = {}
    if config.get("audit", {}).get("save_generated_trace_sets", True):
        trace_paths = _save_trace_sets(output_dir, trace_sets, base_dir=config_path.parent)

    report_json_name = config.get("audit", {}).get("report_json", "optimization_report.json")
    report_md_name = config.get("audit", {}).get("report_md", "optimization_report.md")
    report = {
        "schema_version": "eval_optimize_loop.v1",
        "experiment": {
            "name": config.get("audit", {}).get("experiment_name", "eval_optimize_loop"),
            "started_at": started_at,
            "finished_at": _utc_now(),
        },
        "inputs": {
            "train_evalset": _display_path(train_path, config_path.parent),
            "validation_evalset": _display_path(val_path, config_path.parent),
            "optimizer_config": _display_path(config_path, config_path.parent),
            "prompt_sources": [
                {
                    "name": name,
                    "path": _display_path(Path(target_prompt.describe_source(name)), config_path.parent),
                } for name in target_prompt.names()
            ],
        },
        "baseline": {
            "prompts": baseline_prompts,
            "train": phases["baseline_train"],
            "validation": phases["baseline_validation"],
        },
        "optimization": {
            "backend": backend,
            "seed": seed,
            "optimizer_result": optimizer_summary,
            "rounds": rounds,
            "candidate_prompts": candidate_prompts,
            "model_calls": model.calls,
            "optimizer_model_calls": int(optimizer_summary.get("total_reflection_lm_calls", 0) or 0),
            "total_cost": total_cost,
            "token_usage": total_token_usage,
        },
        "candidate": {
            "prompts": candidate_prompts,
            "train": phases["candidate_train"],
            "validation": phases["candidate_validation"],
        },
        "delta": {
            "train": train_delta,
            "validation": val_delta,
        },
        "gate_decision": gate_decision,
        "failure_attribution": {
            "baseline_train": phases["baseline_train"]["failure_attribution"],
            "baseline_validation": phases["baseline_validation"]["failure_attribution"],
            "candidate_train": phases["candidate_train"]["failure_attribution"],
            "candidate_validation": phases["candidate_validation"]["failure_attribution"],
        },
        "audit": {
            "trace_mode": bool(config.get("audit", {}).get("trace_mode", True)),
            "seed": seed,
            "repro_config": {
                "evaluate": config["evaluate"],
                "optimize": _without_secret_fields(config["optimize"]),
                "gate": config.get("gate", {}),
            },
            "duration_seconds": round(duration, 6),
            "artifacts": {
                "report_json": _display_path(output_dir / report_json_name, config_path.parent),
                "report_md": _display_path(output_dir / report_md_name, config_path.parent),
                "trace_evalsets": trace_paths,
            },
        },
    }

    _write_json(output_dir / report_json_name, report)
    _write_text(output_dir / report_md_name, _render_markdown(report))
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the evaluation + optimization closed-loop example.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--val", type=Path, default=DEFAULT_VAL)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--backend", choices=["fake", "agent_optimizer"], default=None)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        run_pipeline(
            config_path=args.config.resolve(),
            train_path=args.train.resolve(),
            val_path=args.val.resolve(),
            output_dir=args.output_dir.resolve(),
            backend_override=args.backend,
        ))
    decision = report["gate_decision"]["decision"]
    report_path = report["audit"]["artifacts"]["report_json"]
    print(f"Optimization gate decision: {decision}")
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
