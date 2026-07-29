# -*- coding: utf-8 -*-
"""Representative branch coverage for failure attribution."""

from examples.optimization.eval_optimize_loop.pipeline.attributor import (
    _classify_case,
)
from examples.optimization.eval_optimize_loop.pipeline.config import (
    CaseMetricResult,
    CaseResult,
)


def _failed_case(**kwargs) -> CaseResult:
    defaults = {
        "case_id": "case_001",
        "overall_status": "FAILED",
        "metrics": {},
    }
    defaults.update(kwargs)
    return CaseResult(**defaults)


def _failed_metric(name: str) -> CaseMetricResult:
    return CaseMetricResult(
        metric_name=name,
        score=0.0,
        threshold=0.6,
        eval_status="FAILED",
    )


def test_classifies_missing_tool_call():
    case = _failed_case(
        metrics={
            "tool_trajectory_avg_score": _failed_metric(
                "tool_trajectory_avg_score"
            )
        },
        expected_tool_calls=[{"name": "check_stock", "args": {}}],
    )
    record = _classify_case(case, "eval_set")
    assert "tool_call_error" in record.failure_types


def test_classifies_extra_tool_as_overgeneralization():
    case = _failed_case(
        metrics={
            "tool_trajectory_avg_score": _failed_metric(
                "tool_trajectory_avg_score"
            )
        },
        expected_tool_calls=[{"name": "check_stock", "args": {}}],
        actual_tool_calls=[
            {"name": "check_stock", "args": {}},
            {"name": "get_product_price", "args": {}},
        ],
    )
    record = _classify_case(case, "eval_set")
    assert "overgeneralization" in record.failure_types


def test_classifies_format_metric():
    case = _failed_case(
        metrics={"json_schema_score": _failed_metric("json_schema_score")}
    )
    record = _classify_case(case, "eval_set")
    assert "format_error" in record.failure_types


def test_classifies_hallucination_cue_in_case_fallback():
    case = _failed_case(
        actual_response="据我所知大概有货",
        expected_response="库存状态",
    )
    record = _classify_case(case, "eval_set")
    assert "hallucination" in record.failure_types


def test_failed_case_always_has_explainable_fallback():
    record = _classify_case(_failed_case(), "eval_set")
    assert record.failure_types
    assert record.explanation
    assert "missing_information" in record.failure_types


def test_passed_completeness_rubric_is_not_attributed_as_missing():
    metric = CaseMetricResult(
        metric_name="llm_rubric_response",
        score=1 / 3,
        threshold=0.5,
        eval_status="FAILED",
        reason="回答完整；同一信息重复表达；包含用户未询问的推荐信息",
        rubric_scores=[
            {
                "id": "completeness",
                "reason": "库存状态和数量均已回答",
                "score": 1.0,
            },
            {
                "id": "no_redundancy",
                "reason": "库存状态和数量重复多次",
                "score": 0.0,
            },
            {
                "id": "no_extra",
                "reason": "包含推荐和营销话术",
                "score": 0.0,
            },
        ],
    )
    case = _failed_case(
        metrics={"llm_rubric_response": metric},
        expected_response="香蕉库存充足，300件",
        actual_response=(
            "香蕉库存充足，库存充足，300件。建议立即下单。\n"
            "```json\n{\"quantity\": 300}\n```"
        ),
        expected_tool_calls=[
            {"name": "check_stock", "args": {"product": "香蕉"}}
        ],
        actual_tool_calls=[
            {"name": "check_stock", "args": {"product": "香蕉"}}
        ],
    )

    record = _classify_case(case, "eval_set")

    assert "missing_information" not in record.failure_types
    assert "excessive_verbosity" in record.failure_types
    assert "overgeneralization" in record.failure_types
    assert "format_error" in record.failure_types
