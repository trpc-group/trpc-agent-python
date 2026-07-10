from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult

from .models import CaseSnapshot, ToolCallSnapshot


@dataclass(frozen=True)
class FakeResponseSnapshot:
    """Safely parsed details from the deterministic fake response format."""

    payload: dict[str, object] | None
    tool_calls: list[ToolCallSnapshot]


def parse_fake_response(response: str | None) -> FakeResponseSnapshot:
    """Parse fake JSON without allowing malformed output to break reporting."""

    if not response:
        return FakeResponseSnapshot(payload=None, tool_calls=[])
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        return FakeResponseSnapshot(payload=None, tool_calls=[])
    if not isinstance(payload, dict):
        return FakeResponseSnapshot(payload=None, tool_calls=[])
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


def normalize_eval_results(
    results_by_eval_id: dict[str, list[EvalCaseResult]], *, split: Literal["train", "validation"], metric_weights: dict[str, float]
) -> dict[str, CaseSnapshot]:
    snapshots: dict[str, CaseSnapshot] = {}
    for eval_id, runs in results_by_eval_id.items():
        if not runs:
            raise ValueError(f"no evaluator results for {eval_id}")
        metric_scores: dict[str, list[float]] = {}
        metric_thresholds: dict[str, float] = {}
        metric_passed: dict[str, bool] = {}
        metric_reasons: dict[str, list[str]] = {}
        for result in runs:
            for metric in result.overall_eval_metric_results:
                if metric.score is None:
                    raise ValueError(f"metric {metric.metric_name} was not evaluated for {eval_id}")
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
        failure_reasons = [
            f"metric {name} did not meet threshold {metric_thresholds[name]}"
            for name, passed in metric_passed.items()
            if not passed
        ]
        snapshots[eval_id] = CaseSnapshot(
            eval_id=eval_id, split=split, run_count=len(runs), passed=all(item.final_eval_status == EvalStatus.PASSED for item in runs),
            hard_failed=any(item.error_message for item in runs), aggregate_score=weighted / total_weight if total_weight else 0.0,
            metric_scores=averaged, metric_thresholds=metric_thresholds, metric_passed=metric_passed,
            metric_reasons=metric_reasons, failure_reasons=failure_reasons,
            final_response=actual, expected_response=expected,
            trace_digest="sha256:" + hashlib.sha256((actual or "").encode("utf-8")).hexdigest(),
            tool_calls=parsed_actual.tool_calls,
            expected_tool_calls=parsed_expected.tool_calls,
        )
    return snapshots
