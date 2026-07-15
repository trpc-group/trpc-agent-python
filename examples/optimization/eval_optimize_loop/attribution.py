# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Deterministic, evidence-driven failure attribution for stage 3a."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .schemas import AttributionEvidence
from .schemas import CaseEvaluation
from .schemas import FailureAttribution
from .schemas import FailureCategory
from .schemas import StandardizedEvaluation


@dataclass(frozen=True)
class _CandidateReason:
    priority: int
    category: FailureCategory
    summary: str
    evidence: AttributionEvidence


def _json_object(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _case_attribution(case: CaseEvaluation) -> FailureAttribution | None:
    if case.status == "passed":
        return None

    reasons: list[_CandidateReason] = []
    for run in case.runs:
        if run.status == "not_evaluated" or run.error_message:
            summary = run.error_message or "Evaluation did not produce a usable result."
            reasons.append(
                _CandidateReason(
                    priority=10,
                    category="evaluation_error",
                    summary=summary,
                    evidence=AttributionEvidence(
                        evidence_type="execution_error",
                        message=summary,
                        run_id=run.run_id,
                        actual=run.error_message,
                    ),
                )
            )
        for invocation in run.invocations:
            expected_names = [tool.name for tool in invocation.expected_tools]
            actual_names = [tool.name for tool in invocation.actual_tools]
            if expected_names != actual_names and (expected_names or actual_names):
                summary = f"Expected tool names {expected_names}, got {actual_names}."
                reasons.append(
                    _CandidateReason(
                        priority=20,
                        category="tool_name_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="tool",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=expected_names,
                            actual=actual_names,
                        ),
                    )
                )

            expected_arguments = [tool.arguments for tool in invocation.expected_tools]
            actual_arguments = [tool.arguments for tool in invocation.actual_tools]
            if (
                expected_names == actual_names
                and (expected_names or actual_names)
                and expected_arguments != actual_arguments
            ):
                summary = f"Expected tool arguments {expected_arguments}, got {actual_arguments}."
                reasons.append(
                    _CandidateReason(
                        priority=30,
                        category="tool_argument_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="tool",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=expected_arguments,
                            actual=actual_arguments,
                        ),
                    )
                )

            failed_knowledge_metrics = [
                metric
                for metric in invocation.metrics
                if metric.status == "failed" and metric.metric_name == "llm_rubric_knowledge_recall"
            ]
            if failed_knowledge_metrics:
                metric = failed_knowledge_metrics[0]
                summary = metric.reason or "Knowledge recall rubric was not satisfied."
                reasons.append(
                    _CandidateReason(
                        priority=40,
                        category="knowledge_recall",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="metric",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            metric_name=metric.metric_name,
                            actual=metric.reason,
                        ),
                    )
                )

            expected_json = _json_object(invocation.expected_response)
            actual_json = _json_object(invocation.actual_response)
            if expected_json is not None and (
                actual_json is None or not set(expected_json).issubset(actual_json)
            ):
                summary = "Actual response is not valid JSON with the expected top-level fields."
                reasons.append(
                    _CandidateReason(
                        priority=50,
                        category="format_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="response",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=invocation.expected_response,
                            actual=invocation.actual_response,
                        ),
                    )
                )

            failed_rubric_metrics = [
                metric
                for metric in invocation.metrics
                if metric.status == "failed"
                and metric.metric_name.startswith("llm_rubric_")
                and metric.metric_name != "llm_rubric_knowledge_recall"
            ]
            if failed_rubric_metrics:
                metric = failed_rubric_metrics[0]
                summary = metric.reason or f"Rubric metric {metric.metric_name!r} was not satisfied."
                reasons.append(
                    _CandidateReason(
                        priority=60,
                        category="rubric_failure",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="metric",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            metric_name=metric.metric_name,
                            actual=metric.reason,
                        ),
                    )
                )

            if (
                expected_json is not None
                and actual_json is not None
                and expected_json.get("route") != actual_json.get("route")
            ):
                summary = (
                    f"Expected route {expected_json.get('route')!r}, "
                    f"got {actual_json.get('route')!r}."
                )
                reasons.append(
                    _CandidateReason(
                        priority=70,
                        category="routing_error",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="response",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            expected=expected_json.get("route"),
                            actual=actual_json.get("route"),
                        ),
                    )
                )

            failed_final_metrics = [
                metric
                for metric in invocation.metrics
                if metric.status == "failed"
                and metric.metric_name
                in {"final_response_avg_score", "response_match_score", "llm_final_response"}
            ]
            if failed_final_metrics:
                metric = failed_final_metrics[0]
                summary = f"Final response did not satisfy metric {metric.metric_name!r}."
                reasons.append(
                    _CandidateReason(
                        priority=80,
                        category="final_response_mismatch",
                        summary=summary,
                        evidence=AttributionEvidence(
                            evidence_type="response",
                            message=summary,
                            run_id=run.run_id,
                            invocation_id=invocation.invocation_id,
                            metric_name=metric.metric_name,
                            expected=invocation.expected_response,
                            actual=invocation.actual_response,
                        ),
                    )
                )

    if not reasons:
        summary = "Available evaluation evidence does not identify a specific failure category."
        reasons.append(
            _CandidateReason(
                priority=90,
                category="unknown",
                summary=summary,
                evidence=AttributionEvidence(evidence_type="metric", message=summary),
            )
        )

    reasons.sort(key=lambda reason: reason.priority)
    categories: list[FailureCategory] = []
    for reason in reasons:
        if reason.category not in categories:
            categories.append(reason.category)
    return FailureAttribution(
        primary_category=categories[0],
        secondary_categories=categories[1:],
        summary=reasons[0].summary,
        evidence=[reason.evidence for reason in reasons],
    )


def attribute_evaluation(evaluation: StandardizedEvaluation) -> StandardizedEvaluation:
    """Return a copy with deterministic attribution attached to failed cases."""
    return evaluation.model_copy(
        update={
            "cases": [
                case.model_copy(update={"attribution": _case_attribution(case)})
                for case in evaluation.cases
            ]
        }
    )
