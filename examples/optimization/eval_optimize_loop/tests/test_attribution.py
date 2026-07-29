#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Evidence-based failure attribution tests."""

from __future__ import annotations

from attribution import AttributionInput, CATEGORIES, classify, classify_with_reason


def test_categories_complete() -> None:
    assert set(CATEGORIES) == {
        "reply_mismatch",
        "format_fail",
        "tool_call_error",
        "param_error",
        "rubric_fail",
        "knowledge_fail",
        "infrastructure_failure",
        "insufficient_evidence",
        "none",
    }


def test_passed_returns_none() -> None:
    assert classify(AttributionInput(status="PASSED")) == "none"


def test_rubric_fail_from_metric_name() -> None:
    inp = AttributionInput(
        status="FAILED",
        metric_names_failed=("llm_rubric_response",),
        metric_reasons=("reasoning_clear rubric failed",),
    )
    assert classify(inp) == "rubric_fail"


def test_tool_call_error_from_trajectory() -> None:
    inp = AttributionInput(
        status="FAILED",
        expected_tool_uses=({"name": "search", "args": {"q": "x"}},),
        actual_tool_uses=(),
    )
    assert classify(inp) == "tool_call_error"


def test_param_error_exposes_parameter_diff() -> None:
    inp = AttributionInput(
        status="FAILED",
        expected_tool_uses=({"name": "search", "args": {"q": "x"}},),
        actual_tool_uses=({"name": "search", "args": {"q": "y"}},),
    )
    result = classify_with_reason(inp)
    assert result.category == "param_error"
    assert result.evidence["parameter_differences"] == [
        {
            "tool_name": "search",
            "expected_args": {"q": "x"},
            "actual_args": {"q": "y"},
        }
    ]


def test_knowledge_fail_from_evaluator_reason() -> None:
    inp = AttributionInput(
        status="FAILED",
        metric_names_failed=("llm_rubric_knowledge_recall",),
        metric_reasons=("knowledge recall did not support rubric kr1",),
    )
    assert classify(inp) == "knowledge_fail"


def test_format_fail_from_response_evidence() -> None:
    inp = AttributionInput(
        status="FAILED",
        expected_text="步骤：4 + 7 = 11\n答案：11 个",
        actual_text="11",
    )
    assert classify(inp) == "format_fail"


def test_reply_mismatch_from_response_evidence() -> None:
    inp = AttributionInput(
        status="FAILED",
        expected_text="答案：11",
        actual_text="答案：22",
    )
    assert classify(inp) == "reply_mismatch"


def test_infrastructure_failure_is_not_agent_quality_failure() -> None:
    inp = AttributionInput(
        status="NOT_EVALUATED",
        error_message="TimeoutError: provider unavailable",
    )
    assert classify(inp) == "infrastructure_failure"


def test_missing_raw_evidence_is_not_fabricated_reply_mismatch() -> None:
    inp = AttributionInput(
        status="FAILED",
        metric_names_failed=("final_response_avg_score",),
        metric_reasons=("score below threshold",),
    )
    assert classify(inp) == "insufficient_evidence"


def test_scenario_tag_does_not_control_classification() -> None:
    inp = AttributionInput(status="FAILED", scenario_tag="knowledge_lookup")
    assert classify(inp) == "insufficient_evidence"
