"""Deterministic, evidence-based failure attribution without I/O or model calls."""

from __future__ import annotations

from collections import Counter
from typing import Any

from trpc_agent_sdk.evaluation import Invocation, get_all_tool_calls

from .configuration import AttributionConfig
from .models import (
    AttributionPair,
    AttributionSnapshot,
    AttributionStatistics,
    EvaluationSnapshot,
    FailureAttribution,
    FailureCategory,
    SnapshotPair,
)
from .schema import sanitized_text

_KNOWN_METRICS = {
    "tool_trajectory_avg_score": FailureCategory.TOOL_CALL_ERROR,
    "llm_rubric_knowledge_recall": FailureCategory.KNOWLEDGE_RECALL_INSUFFICIENT,
    "llm_rubric_response": FailureCategory.LLM_RUBRIC_NOT_MET,
    "llm_final_response": FailureCategory.LLM_RUBRIC_NOT_MET,
    "response_evaluation_score": FailureCategory.LLM_RUBRIC_NOT_MET,
    "final_response_avg_score": FailureCategory.FINAL_RESPONSE_MISMATCH,
    "response_match_score": FailureCategory.FINAL_RESPONSE_MISMATCH,
}


def _statistics(failures: tuple[FailureAttribution, ...]) -> AttributionStatistics:
    counts = Counter(failure.primary for failure in failures)
    return AttributionStatistics(
        total_failures=len(failures),
        primary_category_counts=dict(counts),
    )


def _safe_text(value: Any, max_chars: int) -> str:
    return sanitized_text(value, max_text_chars=max_chars)


def _tool_calls(invocation: dict[str, Any]) -> list[tuple[str, Any]]:
    parsed = Invocation.model_validate(invocation)
    return [(str(call.name), call.args) for call in get_all_tool_calls(parsed.intermediate_data)]


def _tool_differences(case: Any, max_chars: int) -> list[tuple[FailureCategory, str]]:
    differences: list[tuple[FailureCategory, str]] = []
    for run in case.runs:
        for trace in run.trace:
            actual = _tool_calls(trace["actual"])
            expected_payload = trace["expected"]
            if expected_payload is None:
                continue
            expected = _tool_calls(expected_payload)
            actual_names = [name for name, _ in actual]
            expected_names = [name for name, _ in expected]
            if actual_names != expected_names:
                differences.append((
                    FailureCategory.TOOL_CALL_ERROR,
                    _safe_text({
                        "expectedToolNames": expected_names,
                        "actualToolNames": actual_names
                    }, max_chars),
                ))
                continue
            argument_diffs = [{
                "tool": actual_item[0],
                "expected": expected_item[1],
                "actual": actual_item[1]
            } for actual_item, expected_item in zip(actual, expected) if actual_item[1] != expected_item[1]]
            if argument_diffs:
                differences.append((FailureCategory.TOOL_ARGUMENT_ERROR, _safe_text(argument_diffs, max_chars)))
    return differences


def _fallback_category(reason: str) -> FailureCategory:
    lowered = reason.casefold()
    if "format" in lowered or "schema" in lowered:
        return FailureCategory.FORMAT_VIOLATION
    if "argument" in lowered or "parameter" in lowered:
        return FailureCategory.TOOL_ARGUMENT_ERROR
    if "tool" in lowered or "function" in lowered:
        return FailureCategory.TOOL_CALL_ERROR
    if "knowledge" in lowered or "recall" in lowered or "retriev" in lowered:
        return FailureCategory.KNOWLEDGE_RECALL_INSUFFICIENT
    if "rubric" in lowered or "judge" in lowered:
        return FailureCategory.LLM_RUBRIC_NOT_MET
    if "response" in lowered or "mismatch" in lowered or "answer" in lowered:
        return FailureCategory.FINAL_RESPONSE_MISMATCH
    return FailureCategory.UNKNOWN


def attribute_failures(
    snapshot: EvaluationSnapshot,
    config: AttributionConfig,
    *,
    max_text_chars: int,
) -> AttributionSnapshot:
    """Attribute each failed case using the documented precedence order."""

    failures: list[FailureAttribution] = []
    for case in snapshot.cases:
        if case.passed:
            continue
        candidates: list[tuple[FailureCategory, str]] = []
        reasons: list[str] = []
        evidence: list[str] = []
        trigger_metrics: list[str] = []
        trigger_rubrics: list[str] = []

        def add_candidate(category: FailureCategory, confidence: str) -> None:
            if all(existing != category for existing, _ in candidates):
                candidates.append((category, confidence))

        if case.error:
            add_candidate(FailureCategory.EVALUATION_ERROR, "high")
            reasons.append("Evaluator reported an execution error.")
            evidence.append(_safe_text(case.error, max_text_chars))

        tool_differences = _tool_differences(case, max_text_chars)
        structured_tool_categories = {category for category, _ in tool_differences}
        for category, item_evidence in tool_differences:
            add_candidate(category, "high")
            reasons.append("Structured expected and actual tool trajectories differ.")
            evidence.append(item_evidence)

        for run in case.runs:
            for metric in run.metrics:
                if metric.passed:
                    continue
                trigger_metrics.append(metric.metric_name)
                reason = metric.reason or (
                    f"Metric {metric.metric_name} scored {metric.score:g} below threshold {metric.threshold:g}.")
                reasons.append(_safe_text(reason, max_text_chars))
                evidence.append(
                    _safe_text(
                        {
                            "metric": metric.metric_name,
                            "score": metric.score,
                            "threshold": metric.threshold,
                        },
                        max_text_chars,
                    ))
                for rubric in metric.rubrics:
                    if rubric.passed:
                        continue
                    trigger_rubrics.append(rubric.id)
                    if rubric.id in config.rubric_categories:
                        add_candidate(config.rubric_categories[rubric.id], "high")
                if metric.metric_name in config.metric_categories:
                    add_candidate(config.metric_categories[metric.metric_name], "high")

                semantic = _fallback_category(reason)
                if semantic not in {
                        FailureCategory.UNKNOWN,
                        FailureCategory.FINAL_RESPONSE_MISMATCH,
                }:
                    add_candidate(semantic, "medium")
                if metric.metric_name in _KNOWN_METRICS and not (metric.metric_name == "tool_trajectory_avg_score"
                                                                 and structured_tool_categories):
                    add_candidate(_KNOWN_METRICS[metric.metric_name], "medium")
                if semantic == FailureCategory.FINAL_RESPONSE_MISMATCH:
                    add_candidate(semantic, "medium")

        if not candidates:
            candidates.append((FailureCategory.UNKNOWN, "low"))
        primary, confidence = candidates[0]
        categories = tuple(category for category, _ in candidates)
        failures.append(
            FailureAttribution(
                case_id=case.case_id,
                primary=primary,
                secondary=categories[1:],
                reasons=tuple(dict.fromkeys(reasons or ["The failure has no mapped deterministic cause."])),
                trigger_metrics=tuple(dict.fromkeys(trigger_metrics)),
                trigger_rubrics=tuple(dict.fromkeys(trigger_rubrics)),
                evidence=tuple(dict.fromkeys(evidence or ["No structured evidence was available."])),
                confidence=confidence,
            ))
    attributed = tuple(failures)
    return AttributionSnapshot(
        split=snapshot.split,
        phase=snapshot.phase,
        failures=attributed,
        statistics=_statistics(attributed),
    )


def attribute_pair(
    snapshots: SnapshotPair,
    config: AttributionConfig,
    *,
    max_text_chars: int,
) -> AttributionPair:
    """Attribute a complete train/validation phase without side effects."""

    if snapshots.train is None or snapshots.validation is None:
        raise ValueError("attribution requires complete train and validation snapshots")
    return AttributionPair(
        train=attribute_failures(
            snapshots.train,
            config,
            max_text_chars=max_text_chars,
        ),
        validation=attribute_failures(
            snapshots.validation,
            config,
            max_text_chars=max_text_chars,
        ),
    )


def select_attribution(
    snapshot: AttributionSnapshot,
    case_ids: set[str] | frozenset[str],
) -> AttributionSnapshot:
    """Restrict attribution facts to the cases visible to a downstream stage."""

    selected = tuple(failure for failure in snapshot.failures if failure.case_id in case_ids)
    return AttributionSnapshot(
        split=snapshot.split,
        phase=snapshot.phase,
        failures=selected,
        statistics=_statistics(selected),
    )
