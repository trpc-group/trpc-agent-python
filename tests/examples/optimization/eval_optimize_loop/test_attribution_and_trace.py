from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

from examples.optimization.eval_optimize_loop.pipeline.models import CaseSnapshot, ToolCallSnapshot
from examples.optimization.eval_optimize_loop.pipeline.normalization import normalize_eval_results
from trpc_agent_sdk.evaluation._eval_case import IntermediateData, Invocation
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult, EvalMetricResult, EvalMetricResultPerInvocation
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part


def _case(**overrides: object) -> CaseSnapshot:
    values: dict[str, object] = {
        "eval_id": "case",
        "split": "validation",
        "run_count": 1,
        "passed": False,
        "hard_failed": False,
        "aggregate_score": 0.0,
        "metric_scores": {},
        "metric_thresholds": {},
        "metric_passed": {},
        "trace_digest": "sha256:test",
    }
    values.update(overrides)
    return CaseSnapshot.model_validate(values)


def _attribute(case: CaseSnapshot, judge: Callable | None = None):
    from examples.optimization.eval_optimize_loop.pipeline.attribution import attribute_case

    return attribute_case(case, judge=judge)


def _invocation(
    response: str,
    *,
    tool_uses: list[FunctionCall] | None = None,
    tool_responses: list[FunctionResponse] | None = None,
) -> Invocation:
    return Invocation(
        user_content=Content(role="user", parts=[Part(text="query")]),
        final_response=Content(role="model", parts=[Part(text=response)]),
        intermediate_data=IntermediateData(tool_uses=tool_uses or [], tool_responses=tool_responses or []),
    )


def _normalization_result(actual: Invocation, expected: Invocation) -> EvalCaseResult:
    metric = EvalMetricResult(
        metric_name="final_response_avg_score",
        threshold=1.0,
        score=0.0,
        eval_status=EvalStatus.FAILED,
    )
    return EvalCaseResult(
        eval_set_id="test",
        eval_id="case",
        final_eval_status=EvalStatus.FAILED,
        overall_eval_metric_results=[metric],
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=actual,
                expected_invocation=expected,
                eval_metric_results=[metric],
            )
        ],
        session_id="test",
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (_case(failure_reasons=["request timeout after 30 seconds"]), "timeout"),
        (_case(execution_errors=["backend quota exhausted"]), "execution_error"),
        (_case(failure_reasons=["connection refused while executing request"]), "execution_error"),
        (_case(tool_calls=[], expected_tool_calls=[ToolCallSnapshot(name="lookup_order")]), "tool_selection_error"),
        (_case(tool_calls=[ToolCallSnapshot(name="lookup_order", arguments={"order_id": 7})], expected_tool_calls=[ToolCallSnapshot(name="lookup_order", arguments={"order_id": "A100"})]), "tool_argument_error"),
        (_case(failure_reasons=["tool response was empty"]), "tool_execution_error"),
        (_case(failure_reasons=["actual response: invalid JSON response"]), "format_violation"),
        (_case(metric_passed={"knowledge_recall_score": False}), "knowledge_recall_insufficient"),
        (_case(metric_passed={"fake_rubric_score": False}), "llm_rubric_not_met"),
        (_case(metric_passed={"final_response_avg_score": False}), "final_response_mismatch"),
        (_case(), "unknown"),
    ],
    ids=(
        "timeout",
        "recorded-execution-error",
        "execution-error",
        "tool-selection",
        "tool-arguments",
        "tool-execution",
        "format",
        "knowledge",
        "rubric",
        "final-response",
        "no-rule",
    ),
)
def test_rule_first_attribution_covers_each_precedence_category(case: CaseSnapshot, expected: str) -> None:
    attribution = _attribute(case)

    assert attribution.primary_type.value == expected
    assert attribution.evidence


def test_judge_is_used_only_when_no_rule_matches() -> None:
    calls: list[str] = []

    def judge(_case: CaseSnapshot) -> dict[str, object]:
        calls.append("judge")
        return {"primary_type": "safety_violation", "confidence": 0.8, "evidence": ["judge-only signal"]}

    attribution = _attribute(_case(), judge=judge)

    assert calls == ["judge"]
    assert attribution.primary_type.value == "safety_violation"
    assert attribution.source == "judge"


def test_invalid_judge_output_falls_back_to_unknown() -> None:
    attribution = _attribute(_case(), judge=lambda _case: {})

    assert attribution.primary_type.value == "unknown"
    assert attribution.source == "fallback"
    assert attribution.evidence


def test_structural_rule_wins_over_conflicting_judge() -> None:
    calls: list[str] = []

    def judge(_case: CaseSnapshot) -> dict[str, object]:
        calls.append("judge")
        return {"primary_type": "safety_violation", "confidence": 1.0, "evidence": ["incorrect override"]}

    attribution = _attribute(
        _case(tool_calls=[], expected_tool_calls=[ToolCallSnapshot(name="lookup_order")]), judge=judge
    )

    assert attribution.primary_type.value == "tool_selection_error"
    assert attribution.source == "rule"
    assert calls == []


def test_normalization_uses_intermediate_tools_even_when_final_json_matches() -> None:
    response = '{"route":"order_lookup","tool":"lookup_order","arguments":{"order_id":"A100"},"answer":"ok"}'
    case = normalize_eval_results(
        {
            "case": [
                _normalization_result(
                    _invocation(response, tool_uses=[FunctionCall(name="lookup_refund", args={"order_id": "A100"})]),
                    _invocation(response, tool_uses=[FunctionCall(name="lookup_order", args={"order_id": "A100"})]),
                )
            ]
        },
        split="validation",
        metric_weights={"final_response_avg_score": 1.0},
    )["case"]

    assert case.final_response == case.expected_response
    assert [tool.name for tool in case.tool_calls] == ["lookup_refund"]
    assert [tool.name for tool in case.expected_tool_calls] == ["lookup_order"]
    assert _attribute(case).primary_type.value == "tool_selection_error"


def test_normalization_keeps_explicit_empty_intermediate_tools_over_final_json() -> None:
    response = '{"route":"order_lookup","tool":"lookup_order","arguments":{"order_id":"A100"},"answer":"ok"}'
    case = normalize_eval_results(
        {
            "case": [
                _normalization_result(
                    _invocation(response, tool_uses=[]),
                    _invocation(response, tool_uses=[FunctionCall(name="lookup_order", args={"order_id": "A100"})]),
                )
            ]
        },
        split="validation",
        metric_weights={"final_response_avg_score": 1.0},
    )["case"]

    assert case.tool_calls == []
    assert [tool.name for tool in case.expected_tool_calls] == ["lookup_order"]
    assert _attribute(case).primary_type.value == "tool_selection_error"


def test_normalization_preserves_invalid_tool_argument_shape_for_attribution() -> None:
    response = '{"route":"order_lookup","tool":"lookup_order","arguments":{},"answer":"ok"}'
    invalid_call = FunctionCall.model_construct(name="lookup_order", args=[])
    case = normalize_eval_results(
        {"case": [_normalization_result(_invocation(response, tool_uses=[invalid_call]), _invocation(response, tool_uses=[FunctionCall(name="lookup_order", args={})]))]},
        split="validation",
        metric_weights={"final_response_avg_score": 1.0},
    )["case"]

    assert case.tool_calls[0].arguments == []
    assert case.expected_tool_calls[0].arguments == {}
    assert _attribute(case).primary_type.value == "tool_argument_error"


def test_normalization_attributes_structured_tool_response_error() -> None:
    response = '{"route":"order_lookup","tool":"lookup_order","arguments":{},"answer":"different"}'
    case = normalize_eval_results(
        {
            "case": [
                _normalization_result(
                    _invocation(
                        response,
                        tool_uses=[FunctionCall(name="lookup_order", args={})],
                        tool_responses=[FunctionResponse(name="lookup_order", response={"error": "upstream unavailable"})],
                    ),
                    _invocation(response, tool_uses=[FunctionCall(name="lookup_order", args={})]),
                )
            ]
        },
        split="validation",
        metric_weights={"final_response_avg_score": 1.0},
    )["case"]

    assert case.tool_responses[0].response == {"error": "upstream unavailable"}
    assert _attribute(case).primary_type.value == "tool_execution_error"


@pytest.mark.asyncio
async def test_trace_mode_evaluates_recorded_conversations_without_a_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    from examples.optimization.eval_optimize_loop.run_pipeline import run_trace_pipeline

    report = await run_trace_pipeline(output_dir=tmp_path)

    assert report.mode == "trace"
    assert report.selected_candidate_id is None
    assert report.baseline_validation is not None
    failures = [case for case in report.baseline_validation.cases if not case.passed]
    assert {case.failure_attribution.primary_type.value for case in failures if case.failure_attribution} == {
        "tool_selection_error",
        "tool_argument_error",
        "format_violation",
        "tool_execution_error",
    }
    assert all(case.failure_attribution and case.failure_attribution.evidence for case in failures)
    assert sum(case.passed for case in report.baseline_validation.cases) == 1
    trace_cases = {case.eval_id: case for case in report.baseline_validation.cases}
    intermediate_wins = trace_cases["trace_tool_selection_wrong"]
    assert intermediate_wins.final_response == intermediate_wins.expected_response
    assert [tool.name for tool in intermediate_wins.tool_calls] == ["lookup_refund"]
    assert [tool.name for tool in intermediate_wins.expected_tool_calls] == ["lookup_order"]
    assert (tmp_path / "optimization_report.json").is_file()
    assert (tmp_path / "optimization_report.md").is_file()
    assert (tmp_path / "trace_raw_results.json").is_file()
    assert (tmp_path / "trace_normalized_cases.json").is_file()
    assert json.loads((tmp_path / "trace_raw_results.json").read_text(encoding="utf-8"))["raw_evaluator_ran"] is True


def test_trace_cli_writes_report_paths(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "examples/optimization/eval_optimize_loop/run_pipeline.py",
            "--mode",
            "trace",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "JSON report:" in result.stdout
    assert "Markdown report:" in result.stdout
