# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule-based failure attribution for the eval-optimize-loop example."""

from __future__ import annotations

from typing import Any

from .types import BaselineCaseRecord
from .types import BaselineSplitResult
from .types import FailureAnalysis


class FailureAttributor:
    """Assign a single explainable failure category to each failed case."""

    def annotate_split(self, split: BaselineSplitResult) -> BaselineSplitResult:
        """Attach failure_analysis to failed cases in a split."""
        for case in split.cases:
            case.failure_analysis = self.analyze_case(case)
        return split

    def analyze_case(self, case: BaselineCaseRecord) -> FailureAnalysis | None:
        """Return the best-effort attribution for one failed case."""
        if case.passed:
            return None

        metadata = case.evaluator_metadata or {}
        failed_metrics = _failed_metric_results(metadata)
        expected_tool_calls = _tool_calls(metadata.get("expected_tool_calls"))
        actual_tool_calls = _tool_calls(metadata.get("actual_tool_calls"))

        if _has_failed_metric(failed_metrics, "tool_trajectory"):
            return self._tool_analysis(case, expected_tool_calls, actual_tool_calls, failed_metrics)
        if _has_failed_metric(failed_metrics, "knowledge_recall", "retrieval", "recall"):
            return _analysis(
                "retrieval_failure",
                0.85,
                "Knowledge recall or retrieval quality metric was below threshold.",
                case,
                failed_metrics,
            )
        if _has_failed_metric(failed_metrics, "llm_rubric"):
            return _analysis(
                "llm_rubric_failed",
                0.85,
                "LLM rubric metric was below threshold.",
                case,
                failed_metrics,
            )
        if _has_format_signal(failed_metrics):
            return _analysis(
                "format_error",
                0.8,
                "Output format did not satisfy the configured constraint.",
                case,
                failed_metrics,
            )
        if _has_failed_metric(failed_metrics, "final_response", "response_match"):
            return _analysis(
                "final_answer_mismatch",
                0.9,
                "Final response did not match the reference answer.",
                case,
                failed_metrics,
            )
        return _analysis(
            "unknown",
            0.2,
            _unknown_explanation(metadata),
            case,
            failed_metrics,
        )

    def _tool_analysis(
        self,
        case: BaselineCaseRecord,
        expected_tool_calls: list[dict[str, Any]],
        actual_tool_calls: list[dict[str, Any]],
        failed_metrics: list[dict[str, Any]],
    ) -> FailureAnalysis:
        if _same_tool_names(expected_tool_calls, actual_tool_calls) and expected_tool_calls != actual_tool_calls:
            return _analysis(
                "parameter_error",
                0.9,
                "Tool calls used the expected tools, but arguments differed from the reference trace.",
                case,
                failed_metrics,
                extra_evidence={
                    "expected_tool_calls": expected_tool_calls,
                    "actual_tool_calls": actual_tool_calls,
                },
            )
        return _analysis(
            "tool_call_error",
            0.9,
            "Tool call names, count, or order differed from the reference trace.",
            case,
            failed_metrics,
            extra_evidence={
                "expected_tool_calls": expected_tool_calls,
                "actual_tool_calls": actual_tool_calls,
            },
        )


def _analysis(
    category: str,
    confidence: float,
    explanation: str,
    case: BaselineCaseRecord,
    failed_metrics: list[dict[str, Any]],
    *,
    extra_evidence: dict[str, Any] | None = None,
) -> FailureAnalysis:
    evidence = {
        "case_id": case.id,
        "failed_metrics": [_metric_evidence(metric) for metric in failed_metrics],
        "expected": case.trace.get("expected", ""),
        "actual": case.trace.get("actual", ""),
        "failure_reason": case.failure_reason,
    }
    if extra_evidence:
        evidence.update(extra_evidence)
    return FailureAnalysis(
        category=category,
        confidence=confidence,
        explanation=explanation,
        evidence=evidence,
    )


def _failed_metric_results(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    failed = metadata.get("failed_metric_results")
    if isinstance(failed, list):
        return [metric for metric in failed if isinstance(metric, dict)]

    metrics = metadata.get("overall_metric_results")
    if not isinstance(metrics, list):
        return []

    failed_metrics = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        status = str(metric.get("eval_status") or "").upper()
        score = metric.get("score")
        threshold = metric.get("threshold")
        if status and status != "PASSED":
            failed_metrics.append(metric)
        elif isinstance(score, (int, float)) and isinstance(threshold, (int, float)) and score < threshold:
            failed_metrics.append(metric)
    return failed_metrics


def _has_failed_metric(metrics: list[dict[str, Any]], *needles: str) -> bool:
    for metric in metrics:
        haystack = _metric_text(metric)
        if any(needle in haystack for needle in needles):
            return True
    return False


def _has_format_signal(metrics: list[dict[str, Any]]) -> bool:
    format_needles = ("format", "json", "schema", "regex", "pattern")
    for metric in metrics:
        metric_name = str(metric.get("metric_name") or "").lower()
        if "final_response" in metric_name or "response_match" in metric_name:
            continue
        if any(needle in _metric_text(metric) for needle in format_needles):
            return True
    return False


def _metric_text(metric: dict[str, Any]) -> str:
    parts = [
        metric.get("metric_name"),
        metric.get("reason"),
        metric.get("criterion"),
    ]
    return " ".join(str(part).lower() for part in parts if part)


def _metric_evidence(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "metric_name": metric.get("metric_name"),
        "score": metric.get("score"),
        "threshold": metric.get("threshold"),
        "eval_status": metric.get("eval_status"),
        "reason": metric.get("reason"),
    }


def _tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    calls = []
    for item in value:
        if isinstance(item, dict):
            calls.append({
                "name": item.get("name"),
                "args": item.get("args") or {},
            })
    return calls


def _same_tool_names(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> bool:
    if len(expected) != len(actual):
        return False
    return [call.get("name") for call in expected] == [call.get("name") for call in actual]


def _unknown_explanation(metadata: dict[str, Any]) -> str:
    error_message = metadata.get("error_message")
    if error_message:
        return f"Evaluation did not expose enough structured signals: {error_message}"
    return "Evaluation did not expose enough structured signals for a specific attribution."
