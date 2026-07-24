from __future__ import annotations

import json

import pytest

from examples.optimization.eval_optimize_loop.fake.fake_judge import (
    FakeRubricEvaluator,
    register_fake_rubric_evaluator,
    score_fake_response,
)
from examples.optimization.eval_optimize_loop.pipeline.comparator import compare_case
from examples.optimization.eval_optimize_loop.pipeline.models import CaseSnapshot, FailureType
from examples.optimization.eval_optimize_loop.pipeline.normalization import normalize_eval_results, parse_fake_response
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._eval_case import Invocation
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult, EvalMetricResult, EvalMetricResultPerInvocation
from trpc_agent_sdk.evaluation._eval_set import EvalSet
from trpc_agent_sdk.types import Content, Part


EXPECTED_ORDER = json.dumps(
    {
        "route": "order_lookup",
        "tool": "lookup_order",
        "arguments": {"order_id": "A100"},
        "answer": "正在查询订单 A100。",
    },
    ensure_ascii=False,
)


def _invocation(response: str) -> Invocation:
    return Invocation(
        user_content=Content(role="user", parts=[Part(text="查询订单 A100")]),
        final_response=Content(role="model", parts=[Part(text=response)]),
    )


def _case_result(*, score: float, status: EvalStatus, response: str = EXPECTED_ORDER) -> EvalCaseResult:
    actual = _invocation(response)
    expected = _invocation(EXPECTED_ORDER)
    metric = EvalMetricResult(
        metric_name="fake_rubric_score",
        threshold=0.75,
        score=score,
        eval_status=status,
    )
    return EvalCaseResult(
        eval_set_id="fake",
        eval_id="case",
        final_eval_status=status,
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


def _snapshot(*, failure_types: list[FailureType]) -> CaseSnapshot:
    return CaseSnapshot(
        eval_id="case",
        split="validation",
        run_count=1,
        passed=not failure_types,
        hard_failed=False,
        aggregate_score=1.0 if not failure_types else 0.0,
        metric_scores={"fake_rubric_score": 1.0 if not failure_types else 0.0},
        metric_thresholds={"fake_rubric_score": 0.75},
        metric_passed={"fake_rubric_score": not failure_types},
        trace_digest="sha256:test",
        failure_types=failure_types,
    )


def test_fake_rubric_scores_invalid_json_as_zero_with_parse_reason() -> None:
    evaluator = FakeRubricEvaluator(threshold=0.75)
    result = evaluator.evaluate_invocations([_invocation("not json")], [_invocation(EXPECTED_ORDER)])

    assert score_fake_response("not json", EXPECTED_ORDER) == 0.0
    assert result.overall_score == 0.0
    assert result.per_invocation_results[0].reason == "invalid JSON response"


def test_fake_rubric_rejects_mismatched_invocation_lengths() -> None:
    evaluator = FakeRubricEvaluator(threshold=0.75)

    with pytest.raises(ValueError, match="same number of invocations"):
        evaluator.evaluate_invocations(
            [_invocation(EXPECTED_ORDER), _invocation(EXPECTED_ORDER)],
            [_invocation(EXPECTED_ORDER)],
        )


def test_fake_rubric_scores_exact_response_and_required_argument_deterministically() -> None:
    missing_argument = json.dumps(
        {
            "route": "order_lookup",
            "tool": "lookup_order",
            "arguments": {},
            "answer": "正在查询订单。",
        },
        ensure_ascii=False,
    )

    assert score_fake_response(EXPECTED_ORDER, EXPECTED_ORDER) == 1.0
    assert score_fake_response(missing_argument, EXPECTED_ORDER) == 0.75
    assert score_fake_response('{"route":"order_lookup","tool":"lookup_order","arguments":{"order_id":"A100"}}', EXPECTED_ORDER) == 0.25


@pytest.mark.asyncio
async def test_registry_evaluates_fake_evalset_with_both_metrics_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    register_fake_rubric_evaluator()
    eval_set = EvalSet.model_validate(
        {
            "eval_set_id": "fake-rubric",
            "eval_cases": [
                {
                    "eval_id": "case",
                    "conversation": [
                        {
                            "user_content": {"role": "user", "parts": [{"text": "查询订单 A100"}]},
                            "final_response": {"role": "model", "parts": [{"text": EXPECTED_ORDER}]},
                        }
                    ],
                }
            ],
        }
    )
    config = EvalConfig.model_validate(
        {
            "metrics": [
                {"metric_name": "final_response_avg_score", "threshold": 1.0},
                {"metric_name": "fake_rubric_score", "threshold": 0.75},
            ]
        }
    )

    async def call_agent(_query: str) -> str:
        return EXPECTED_ORDER

    _, _, _, results = await AgentEvaluator.evaluate_eval_set(
        eval_set,
        call_agent=call_agent,
        eval_config=config,
        num_runs=1,
        print_detailed_results=False,
    )

    assert {metric.metric_name for metric in results["case"][0].overall_eval_metric_results} == {
        "final_response_avg_score",
        "fake_rubric_score",
    }
    assert all(metric.score == 1.0 for metric in results["case"][0].overall_eval_metric_results)


def test_normalization_averages_all_runs_but_requires_every_run_to_pass() -> None:
    snapshots = normalize_eval_results(
        {
            "case": [
                _case_result(score=1.0, status=EvalStatus.PASSED),
                _case_result(score=0.0, status=EvalStatus.FAILED, response="not json"),
            ]
        },
        split="validation",
        metric_weights={"fake_rubric_score": 1.0},
    )

    case = snapshots["case"]
    assert case.run_count == 2
    assert case.metric_scores["fake_rubric_score"] == 0.5
    assert case.metric_passed["fake_rubric_score"] is False
    assert case.passed is False
    assert any("invalid JSON" in reason for reason in case.failure_reasons)
    assert FailureType.FORMAT_VIOLATION in case.failure_types


def test_normalization_rejects_metrics_missing_from_an_earlier_run() -> None:
    first_run = _case_result(score=1.0, status=EvalStatus.PASSED)
    second_run = _case_result(score=1.0, status=EvalStatus.PASSED)
    extra_metric = EvalMetricResult(
        metric_name="final_response_avg_score",
        threshold=1.0,
        score=1.0,
        eval_status=EvalStatus.PASSED,
    )
    second_run.overall_eval_metric_results.append(extra_metric)
    second_run.eval_metric_result_per_invocation[0].eval_metric_results.append(extra_metric)

    with pytest.raises(ValueError, match="inconsistent metrics"):
        normalize_eval_results(
            {"case": [first_run, second_run]},
            split="validation",
            metric_weights={"fake_rubric_score": 1.0},
        )


def test_fake_json_extraction_and_failure_type_deltas() -> None:
    valid = parse_fake_response(EXPECTED_ORDER)
    invalid = parse_fake_response("not json")

    assert valid.tool_calls[0].arguments == {"order_id": "A100"}
    assert invalid.tool_calls == []
    assert invalid.failure_reason == "invalid JSON response"

    delta = compare_case(
        _snapshot(failure_types=[FailureType.FORMAT_VIOLATION, FailureType.TOOL_ARGUMENT_ERROR]),
        _snapshot(failure_types=[FailureType.TOOL_SELECTION_ERROR]),
        epsilon=1e-6,
        critical_case_ids=set(),
    )
    assert delta.new_failure_types == [FailureType.TOOL_SELECTION_ERROR]
    assert delta.resolved_failure_types == [FailureType.FORMAT_VIOLATION, FailureType.TOOL_ARGUMENT_ERROR]


@pytest.mark.asyncio
async def test_real_evaluator_normalization_records_later_malformed_invocations_and_delta_types() -> None:
    register_fake_rubric_evaluator()
    eval_set = EvalSet.model_validate(
        {
            "eval_set_id": "fake-rubric-integration",
            "eval_cases": [
                {
                    "eval_id": "case",
                    "conversation": [
                        {
                            "user_content": {"role": "user", "parts": [{"text": "first"}]},
                            "final_response": {"role": "model", "parts": [{"text": EXPECTED_ORDER}]},
                        },
                        {
                            "user_content": {"role": "user", "parts": [{"text": "second"}]},
                            "final_response": {"role": "model", "parts": [{"text": EXPECTED_ORDER}]},
                        },
                    ],
                }
            ],
        }
    )
    config = EvalConfig.model_validate({"metrics": [{"metric_name": "fake_rubric_score", "threshold": 0.75}]})

    async def baseline_agent(_query: str) -> str:
        return EXPECTED_ORDER

    async def malformed_agent(query: str) -> str:
        return "not json" if query == "second" else EXPECTED_ORDER

    _, _, _, baseline_results = await AgentEvaluator.evaluate_eval_set(
        eval_set, call_agent=baseline_agent, eval_config=config, num_runs=1, print_detailed_results=False
    )
    _, _, _, malformed_results = await AgentEvaluator.evaluate_eval_set(
        eval_set, call_agent=malformed_agent, eval_config=config, num_runs=1, print_detailed_results=False
    )

    baseline = normalize_eval_results(
        baseline_results, split="validation", metric_weights={"fake_rubric_score": 1.0}
    )["case"]
    malformed = normalize_eval_results(
        malformed_results, split="validation", metric_weights={"fake_rubric_score": 1.0}
    )["case"]
    delta = compare_case(baseline, malformed, epsilon=1e-6, critical_case_ids=set())

    assert "actual response: invalid JSON response" in malformed.failure_reasons
    assert FailureType.FORMAT_VIOLATION in malformed.failure_types
    assert delta.new_failure_types == [FailureType.FORMAT_VIOLATION]


@pytest.mark.asyncio
async def test_real_evaluator_missing_required_fields_produce_delta_failure_type() -> None:
    register_fake_rubric_evaluator()
    eval_set = EvalSet.model_validate(
        {
            "eval_set_id": "fake-rubric-schema",
            "eval_cases": [
                {
                    "eval_id": "case",
                    "conversation": [
                        {
                            "user_content": {"role": "user", "parts": [{"text": "schema"}]},
                            "final_response": {"role": "model", "parts": [{"text": EXPECTED_ORDER}]},
                        }
                    ],
                }
            ],
        }
    )
    config = EvalConfig.model_validate({"metrics": [{"metric_name": "fake_rubric_score", "threshold": 0.75}]})
    missing_answer = '{"route":"order_lookup","tool":"lookup_order","arguments":{"order_id":"A100"}}'

    async def baseline_agent(_query: str) -> str:
        return EXPECTED_ORDER

    async def missing_field_agent(_query: str) -> str:
        return missing_answer

    _, _, _, baseline_results = await AgentEvaluator.evaluate_eval_set(
        eval_set, call_agent=baseline_agent, eval_config=config, num_runs=1, print_detailed_results=False
    )
    _, _, _, missing_field_results = await AgentEvaluator.evaluate_eval_set(
        eval_set, call_agent=missing_field_agent, eval_config=config, num_runs=1, print_detailed_results=False
    )

    baseline = normalize_eval_results(
        baseline_results, split="validation", metric_weights={"fake_rubric_score": 1.0}
    )["case"]
    missing_field = normalize_eval_results(
        missing_field_results, split="validation", metric_weights={"fake_rubric_score": 1.0}
    )["case"]
    delta = compare_case(baseline, missing_field, epsilon=1e-6, critical_case_ids=set())

    assert FailureType.FORMAT_VIOLATION in missing_field.failure_types
    assert delta.new_failure_types == [FailureType.FORMAT_VIOLATION]
