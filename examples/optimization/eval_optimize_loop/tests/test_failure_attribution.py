from __future__ import annotations

from trpc_agent_sdk.evaluation import (
    EvalCaseResult,
    EvalMetricResult,
    EvalMetricResultPerInvocation,
    EvalStatus,
    Invocation,
)
from trpc_agent_sdk.types import Content, Part

from ..failure_attribution import attribute_failures
from ..models import FailureAttribution


def _make_invocation(text: str, invocation_id: str = "t1") -> Invocation:
    return Invocation(
        invocation_id=invocation_id,
        user_content=Content(parts=[Part.from_text(text="query")], role="user"),
        final_response=Content(parts=[Part.from_text(text=text)], role="model"),
    )


def _make_metric_result(metric_name: str, score: float, threshold: float) -> EvalMetricResult:
    status = EvalStatus.PASSED if score >= threshold else EvalStatus.FAILED
    return EvalMetricResult(metric_name=metric_name, score=score, threshold=threshold, eval_status=status)


def _make_case_result(
    case_id: str,
    metric_results: list[EvalMetricResult],
    actual_text: str = "",
    expected_text: str = "",
) -> EvalCaseResult:
    actual_inv = _make_invocation(actual_text)
    expected_inv = _make_invocation(expected_text)
    return EvalCaseResult(
        eval_set_id="test_set",
        eval_id=case_id,
        final_eval_status=EvalStatus.FAILED if any(m.eval_status == EvalStatus.FAILED for m in metric_results) else EvalStatus.PASSED,
        overall_eval_metric_results=metric_results,
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=actual_inv,
                expected_invocation=expected_inv,
                eval_metric_results=metric_results,
            )
        ],
        session_id="session1",
    )


def test_attribute_failures_all_pass():
    """All cases pass -- empty attribution."""
    results = {
        "case_a": [
            _make_case_result("case_a", [_make_metric_result("final_response_avg_score", 1.0, 1.0)])
        ],
        "case_b": [
            _make_case_result("case_b", [_make_metric_result("final_response_avg_score", 1.0, 1.0)])
        ],
    }
    attr = attribute_failures(results)
    assert isinstance(attr, FailureAttribution)
    assert attr.total_cases == 2
    assert attr.failed_cases == 0
    assert len(attr.categories) == 0


def test_attribute_failures_final_response_mismatch():
    """Final response mismatch detected."""
    results = {
        "case_a": [
            _make_case_result(
                "case_a",
                [_make_metric_result("final_response_avg_score", 0.0, 1.0)],
                actual_text="wrong answer",
                expected_text="expected answer",
            )
        ],
    }
    attr = attribute_failures(results)
    assert attr.failed_cases == 1
    assert "final_response_mismatch" in attr.categories
    assert attr.categories["final_response_mismatch"].count == 1
    assert "case_a" in attr.categories["final_response_mismatch"].case_ids


def test_attribute_failures_multiple_categories():
    """Case failing on multiple metrics gets attributed to all categories."""
    results = {
        "case_x": [
            _make_case_result(
                "case_x",
                [
                    _make_metric_result("final_response_avg_score", 0.0, 1.0),
                    _make_metric_result("tool_trajectory_avg_score", 0.0, 1.0),
                ],
            )
        ],
    }
    attr = attribute_failures(results)
    assert attr.failed_cases == 1
    assert "final_response_mismatch" in attr.categories
    assert "tool_trajectory_mismatch" in attr.categories


def test_attribute_failures_format_violation():
    """Detect format violation when response is missing required prefix."""
    results = {
        "case_z": [
            _make_case_result(
                "case_z",
                [_make_metric_result("final_response_avg_score", 0.0, 1.0)],
                actual_text="the result is 42",
                expected_text="答案：42",
            )
        ],
    }
    attr = attribute_failures(results)
    assert "format_violation" in attr.categories
    assert attr.categories["format_violation"].count == 1


def test_attribute_failures_unknown_metric():
    """Unknown metric name falls into unknown_metric_failure."""
    results = {
        "case_u": [
            _make_case_result("case_u", [_make_metric_result("custom_custom_metric", 0.0, 0.8)])
        ],
    }
    attr = attribute_failures(results)
    assert attr.failed_cases == 1
    assert "unknown_metric_failure" in attr.categories


def test_attribute_failures_empty_case_results():
    """Case with empty list of results is skipped."""
    results = {
        "case_empty": [],
        "case_b": [
            _make_case_result("case_b", [_make_metric_result("final_response_avg_score", 0.0, 1.0)])
        ],
    }
    attr = attribute_failures(results)
    assert attr.total_cases == 2
    assert attr.failed_cases == 1
    assert "final_response_mismatch" in attr.categories
    assert "case_b" in attr.categories["final_response_mismatch"].case_ids


def test_attribute_failures_mixed_metrics():
    """Only failed metrics are attributed; passed ones are skipped."""
    results = {
        "case_m": [
            _make_case_result(
                "case_m",
                [
                    _make_metric_result("final_response_avg_score", 0.0, 1.0),
                    _make_metric_result("tool_trajectory_avg_score", 1.0, 1.0),
                ],
            )
        ],
    }
    attr = attribute_failures(results)
    assert attr.failed_cases == 1
    assert "final_response_mismatch" in attr.categories
    assert "tool_trajectory_mismatch" not in attr.categories
