from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult
from trpc_agent_sdk.evaluation._eval_case import get_all_tool_calls, get_all_tool_responses

from .models import CaseSnapshot, FailureType, ToolCallSnapshot, ToolResponseSnapshot


@dataclass(frozen=True)
class FakeResponseSnapshot:
    """Safely parsed details from the deterministic fake response format."""

    payload: dict[str, object] | None
    tool_calls: list[ToolCallSnapshot]
    failure_reason: str | None = None


def parse_fake_response(response: str | None) -> FakeResponseSnapshot:
    """Parse fake JSON without allowing malformed output to break reporting."""

    if not response:
        return FakeResponseSnapshot(payload=None, tool_calls=[], failure_reason="missing fake JSON response")
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        return FakeResponseSnapshot(payload=None, tool_calls=[], failure_reason="invalid JSON response")
    if not isinstance(payload, dict):
        return FakeResponseSnapshot(payload=None, tool_calls=[], failure_reason="fake JSON response must be an object")
    tool = payload.get("tool")
    arguments = payload.get("arguments")
    if isinstance(tool, str) and tool not in {"", "none"}:
        tool_calls = [ToolCallSnapshot(name=tool, arguments=arguments if isinstance(arguments, dict) else {})]
    else:
        tool_calls = []
    return FakeResponseSnapshot(payload=payload, tool_calls=tool_calls)


def _text(content: object) -> str | None:
    parts = getattr(content, "parts", None)
    if not parts:
        return None
    return "".join(part.text or "" for part in parts)


def _tool_call_snapshot(tool_call: object) -> ToolCallSnapshot:
    return ToolCallSnapshot(
        name=str(getattr(tool_call, "name", "") or ""),
        arguments=getattr(tool_call, "args", None),
    )


def _tool_response_snapshot(tool_response: object) -> ToolResponseSnapshot:
    return ToolResponseSnapshot(
        name=str(getattr(tool_response, "name", "") or ""),
        response=getattr(tool_response, "response", None),
    )


def normalize_eval_results(
    results_by_eval_id: dict[str, list[EvalCaseResult]], *, split: Literal["train", "validation"], metric_weights: dict[str, float]
) -> dict[str, CaseSnapshot]:
    snapshots: dict[str, CaseSnapshot] = {}
    for eval_id, runs in results_by_eval_id.items():
        if not runs:
            raise ValueError(f"evaluation error for {eval_id}: no evaluator results")
        metric_scores: dict[str, list[float]] = {}
        metric_thresholds: dict[str, float] = {}
        metric_passed: dict[str, bool] = {}
        metric_reasons: dict[str, list[str]] = {}
        expected_metric_names: set[str] | None = None
        for run_index, result in enumerate(runs, start=1):
            current_metric_names = {metric.metric_name for metric in result.overall_eval_metric_results}
            if expected_metric_names is None:
                expected_metric_names = current_metric_names
            elif current_metric_names != expected_metric_names:
                missing = expected_metric_names - current_metric_names
                unexpected = current_metric_names - expected_metric_names
                raise ValueError(
                    f"evaluation error for {eval_id}: run {run_index} has inconsistent metrics; "
                    f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
                )
            if not current_metric_names:
                raise ValueError(f"evaluation error for {eval_id}: run {run_index} has no metric results")
            for metric in result.overall_eval_metric_results:
                if metric.eval_status == EvalStatus.NOT_EVALUATED or metric.score is None:
                    raise ValueError(
                        f"evaluation error for {eval_id}: metric {metric.metric_name} in run {run_index} is NOT_EVALUATED or missing a score"
                    )
                metric_scores.setdefault(metric.metric_name, []).append(metric.score)
                metric_thresholds[metric.metric_name] = metric.threshold
                current_passed = metric.eval_status == EvalStatus.PASSED
                metric_passed[metric.metric_name] = metric_passed.get(metric.metric_name, True) and current_passed
                if metric.details and metric.details.reason:
                    metric_reasons.setdefault(metric.metric_name, []).append(metric.details.reason)
        averaged = {name: sum(values) / len(values) for name, values in metric_scores.items()}
        weighted = sum(averaged[name] * metric_weights.get(name, 1.0) for name in averaged)
        total_weight = sum(metric_weights.get(name, 1.0) for name in averaged)
        first = runs[0]
        invocation = first.eval_metric_result_per_invocation[0] if first.eval_metric_result_per_invocation else None
        actual = _text(invocation.actual_invocation.final_response) if invocation else None
        expected = _text(invocation.expected_invocation.final_response) if invocation and invocation.expected_invocation else None
        parsed_actual = parse_fake_response(actual)
        parsed_expected = parse_fake_response(expected)
        first_invocations = first.eval_metric_result_per_invocation
        intermediate_tool_calls = [
            _tool_call_snapshot(tool_call)
            for run_invocation in first_invocations
            for tool_call in get_all_tool_calls(run_invocation.actual_invocation.intermediate_data)
        ]
        intermediate_expected_tool_calls = [
            _tool_call_snapshot(tool_call)
            for run_invocation in first_invocations
            if run_invocation.expected_invocation is not None
            for tool_call in get_all_tool_calls(run_invocation.expected_invocation.intermediate_data)
        ]
        actual_has_intermediate_data = any(
            run_invocation.actual_invocation.intermediate_data is not None
            for run_invocation in first_invocations
        )
        expected_has_intermediate_data = any(
            run_invocation.expected_invocation is not None
            and run_invocation.expected_invocation.intermediate_data is not None
            for run_invocation in first_invocations
        )
        tool_calls = intermediate_tool_calls if actual_has_intermediate_data else parsed_actual.tool_calls
        expected_tool_calls = (
            intermediate_expected_tool_calls if expected_has_intermediate_data else parsed_expected.tool_calls
        )
        tool_responses = [
            _tool_response_snapshot(tool_response)
            for run_invocation in first_invocations
            for tool_response in get_all_tool_responses(run_invocation.actual_invocation.intermediate_data)
        ]
        parsed_run_responses = [
            (
                parse_fake_response(_text(run_invocation.actual_invocation.final_response)),
                parse_fake_response(_text(run_invocation.expected_invocation.final_response))
                if run_invocation.expected_invocation
                else FakeResponseSnapshot(payload=None, tool_calls=[], failure_reason="missing expected response"),
            )
            for run in runs
            for run_invocation in run.eval_metric_result_per_invocation
        ]
        failure_reasons = [
            f"metric {name} did not meet threshold {metric_thresholds[name]}"
            for name, passed in metric_passed.items()
            if not passed
        ]
        for name, passed in metric_passed.items():
            if not passed:
                failure_reasons.extend(metric_reasons.get(name, []))
        for parsed, response_name in [
            (parsed, response_name)
            for actual_parsed, expected_parsed in parsed_run_responses
            for parsed, response_name in ((actual_parsed, "actual"), (expected_parsed, "expected"))
        ]:
            if parsed.failure_reason:
                failure_reasons.append(f"{response_name} response: {parsed.failure_reason}")
        failure_types = _failure_types(
            runs,
            metric_passed,
            metric_reasons,
            [actual_parsed for actual_parsed, _ in parsed_run_responses],
            [expected_parsed for _, expected_parsed in parsed_run_responses],
        )
        execution_errors = [run.error_message for run in runs if run.error_message]
        snapshots[eval_id] = CaseSnapshot(
            eval_id=eval_id, split=split, run_count=len(runs), passed=all(item.final_eval_status == EvalStatus.PASSED for item in runs),
            hard_failed=any(item.error_message for item in runs), aggregate_score=weighted / total_weight if total_weight else 0.0,
            metric_scores=averaged, metric_thresholds=metric_thresholds, metric_passed=metric_passed,
            metric_reasons=metric_reasons, execution_errors=execution_errors, failure_reasons=failure_reasons, failure_types=failure_types,
            final_response=actual, expected_response=expected,
            trace_digest="sha256:" + hashlib.sha256((actual or "").encode("utf-8")).hexdigest(),
            tool_calls=tool_calls,
            expected_tool_calls=expected_tool_calls,
            tool_responses=tool_responses,
        )
    return snapshots


def _failure_types(
    runs: list[EvalCaseResult],
    metric_passed: dict[str, bool],
    metric_reasons: dict[str, list[str]],
    actual: list[FakeResponseSnapshot],
    expected: list[FakeResponseSnapshot],
) -> list[FailureType]:
    failure_types: set[FailureType] = set()
    error_messages = [run.error_message for run in runs if run.error_message]
    if any("timeout" in error.lower() for error in error_messages):
        failure_types.add(FailureType.TIMEOUT)
    elif error_messages:
        failure_types.add(FailureType.EXECUTION_ERROR)
    if any(item.failure_reason for item in actual + expected):
        failure_types.add(FailureType.FORMAT_VIOLATION)
    for metric_name, passed in metric_passed.items():
        if passed:
            continue
        reasons = " ".join(metric_reasons.get(metric_name, [])).lower()
        if "route or tool" in reasons:
            failure_types.add(FailureType.TOOL_SELECTION_ERROR)
        if "tool arguments" in reasons:
            failure_types.add(FailureType.TOOL_ARGUMENT_ERROR)
        if "tool response" in reasons and any(marker in reasons for marker in ("failed", "error", "empty")):
            failure_types.add(FailureType.TOOL_EXECUTION_ERROR)
        if "refuse to guess" in reasons:
            failure_types.add(FailureType.KNOWLEDGE_RECALL_INSUFFICIENT)
        if metric_name == "final_response_avg_score":
            failure_types.add(FailureType.FINAL_RESPONSE_MISMATCH)
        if metric_name == "fake_rubric_score":
            if "invalid json" in reasons or "missing required fields" in reasons:
                failure_types.add(FailureType.FORMAT_VIOLATION)
    return sorted(failure_types, key=lambda item: item.value)
