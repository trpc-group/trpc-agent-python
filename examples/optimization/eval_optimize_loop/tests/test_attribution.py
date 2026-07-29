# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Failure attribution tests."""

from __future__ import annotations

from pipeline import FailureAttributor

from .conftest import case_record
from .conftest import metric_result


def test_attributor_classifies_all_supported_failure_categories() -> None:
    attributor = FailureAttributor()
    cases = [
        (
            case_record(
                "final_answer",
                passed=False,
                metric_results=[
                    metric_result(
                        "final_response_avg_score",
                        reason="Final response does not match the reference answer.",
                    )
                ],
            ),
            "final_answer_mismatch",
        ),
        (
            case_record(
                "tool_call",
                passed=False,
                metric_results=[metric_result("tool_trajectory_avg_score")],
                expected_tool_calls=[{
                    "name": "search",
                    "args": {
                        "query": "apple"
                    }
                }],
                actual_tool_calls=[{
                    "name": "lookup",
                    "args": {
                        "query": "apple"
                    }
                }],
            ),
            "tool_call_error",
        ),
        (
            case_record(
                "parameter",
                passed=False,
                metric_results=[metric_result("tool_trajectory_avg_score")],
                expected_tool_calls=[{
                    "name": "search",
                    "args": {
                        "query": "apple"
                    }
                }],
                actual_tool_calls=[{
                    "name": "search",
                    "args": {
                        "query": "banana"
                    }
                }],
            ),
            "parameter_error",
        ),
        (
            case_record(
                "rubric",
                passed=False,
                metric_results=[metric_result("llm_rubric_response", reason="LLM rubric score is below threshold.")],
            ),
            "llm_rubric_failed",
        ),
        (
            case_record(
                "retrieval",
                passed=False,
                metric_results=[metric_result("llm_rubric_knowledge_recall", reason="Knowledge recall failed.")],
            ),
            "retrieval_failure",
        ),
        (
            case_record(
                "format",
                passed=False,
                metric_results=[metric_result("response_format_score", reason="Output format is invalid.")],
            ),
            "format_error",
        ),
        (
            case_record(
                "unknown",
                passed=False,
                metric_results=[metric_result("custom_metric", reason=None)],
            ),
            "unknown",
        ),
    ]

    for case, expected_category in cases:
        analysis = attributor.analyze_case(case)
        assert analysis is not None
        assert analysis.category == expected_category
        assert analysis.explanation
        assert analysis.evidence["case_id"] == case.id


def test_attributor_returns_none_for_passed_case() -> None:
    case = case_record(
        "passed",
        passed=True,
        metric_results=[metric_result("final_response_avg_score", score=1.0, eval_status="PASSED")],
    )

    assert FailureAttributor().analyze_case(case) is None
