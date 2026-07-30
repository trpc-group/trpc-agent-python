"""Dataset, normalization and comparison tests."""

from __future__ import annotations

from copy import deepcopy

import pytest

from trpc_agent_sdk.evaluation import (
    EvalCaseResult,
    EvalConfig,
    EvalMetricResult,
    EvalMetricResultPerInvocation,
    EvalSet,
    EvalSetAggregateResult,
    EvalStatus,
    EvaluateResult,
)

from examples.optimization.eval_optimize_loop.pipeline.evaluation import (
    compare_snapshots,
    normalize_result,
    split_train_dataset,
    validate_datasets,
)
from examples.optimization.eval_optimize_loop.pipeline.models import Phase, Split, Transition


def _eval_set(set_id: str, case_ids: list[str]) -> EvalSet:
    return EvalSet.model_validate({
        "evalSetId":
        set_id,
        "evalCases": [{
            "evalId":
            case_id,
            "conversation": [{
                "invocationId": f"input-{case_id}",
                "userContent": {
                    "role": "user",
                    "parts": [{
                        "text": f"query {case_id}"
                    }]
                },
                "finalResponse": {
                    "role": "model",
                    "parts": [{
                        "text": "expected"
                    }]
                },
            }],
            "sessionInput": {
                "appName": "loop-test",
                "userId": "test",
                "state": {}
            },
        } for case_id in case_ids],
    })


def _raw(eval_set: EvalSet, scores: dict[str, list[float]], threshold: float = 0.5) -> EvaluateResult:
    by_case: dict[str, list[EvalCaseResult]] = {}
    for case in eval_set.eval_cases:
        runs = []
        expected = case.conversation[0]
        for run_id, score in enumerate(scores[case.eval_id], 1):
            passed = score >= threshold
            status = EvalStatus.PASSED if passed else EvalStatus.FAILED
            actual = expected.model_copy(
                update={
                    "invocation_id": f"actual-{case.eval_id}-{run_id}",
                    "final_response": expected.final_response,
                },
                deep=True,
            )
            metric = EvalMetricResult(
                metric_name="quality",
                threshold=threshold,
                score=score,
                eval_status=status,
            )
            runs.append(
                EvalCaseResult(
                    eval_set_id=eval_set.eval_set_id,
                    eval_id=case.eval_id,
                    run_id=run_id,
                    final_eval_status=status,
                    overall_eval_metric_results=[metric],
                    eval_metric_result_per_invocation=[
                        EvalMetricResultPerInvocation(
                            actual_invocation=actual,
                            expected_invocation=expected,
                            eval_metric_results=[metric],
                        )
                    ],
                    session_id=f"session-{run_id}",
                    user_id="test",
                ))
        by_case[case.eval_id] = runs
    return EvaluateResult(
        results_by_eval_set_id={
            eval_set.eval_set_id:
            EvalSetAggregateResult(
                eval_results_by_eval_id=by_case,
                num_runs=len(next(iter(scores.values()))),
            )
        })


def test_dataset_validation_rejects_content_leakage() -> None:
    train = _eval_set("train", ["a", "b", "c"])
    validation = _eval_set("validation", ["v"])
    leaked = train.eval_cases[0].model_copy(update={"eval_id": "v"}, deep=True)
    validation = validation.model_copy(update={"eval_cases": [leaked]}, deep=True)
    with pytest.raises(ValueError, match="duplicate case content"):
        validate_datasets(
            train,
            validation,
            train_path="train.json",
            validation_path="validation.json",
            configured_metrics=["quality"],
        )


def test_dataset_validation_rejects_same_input_with_changed_reference() -> None:
    train = _eval_set("train", ["a", "b", "c"])
    validation = _eval_set("validation", ["v"])
    leaked = train.eval_cases[0].model_copy(update={"eval_id": "v"}, deep=True)
    leaked.conversation[0].final_response.parts[0].text = "different label"
    validation = validation.model_copy(update={"eval_cases": [leaked]}, deep=True)
    with pytest.raises(ValueError, match="duplicate model inputs"):
        validate_datasets(
            train,
            validation,
            train_path="train.json",
            validation_path="validation.json",
            configured_metrics=["quality"],
        )


@pytest.mark.parametrize("metrics", [[], ["quality", "quality"]])
def test_dataset_validation_rejects_invalid_metric_schema(metrics) -> None:
    with pytest.raises(ValueError, match="metric names"):
        validate_datasets(
            _eval_set("train", ["a", "b", "c"]),
            _eval_set("validation", ["v"]),
            train_path="train.json",
            validation_path="validation.json",
            configured_metrics=metrics,
        )


def test_normalization_aggregates_runs_and_inner_split_is_deterministic() -> None:
    train = _eval_set("train", ["a", "b", "c"])
    config = EvalConfig(metrics=[{"metric_name": "quality", "threshold": 0.5}], num_runs=2)
    snapshot = normalize_result(
        _raw(train, {
            "a": [1, 1],
            "b": [0, 1],
            "c": [0, 0]
        }),
        train,
        config,
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        case_weights={"a": 2},
    )
    assert snapshot.case_ids == ("a", "b", "c")
    assert snapshot.cases[1].score == 0.5
    assert snapshot.cases[1].passed is False
    assert snapshot.dataset_score == pytest.approx(0.625)
    first = split_train_dataset(train, snapshot, seed=42, selection_ratio=0.34)
    second = split_train_dataset(train, snapshot, seed=42, selection_ratio=0.34)
    assert [case.eval_id for case in first[0].eval_cases] == [case.eval_id for case in second[0].eval_cases]
    assert len(first[0].eval_cases) == 2
    assert len(first[1].eval_cases) == 1


def test_normalization_fails_on_threshold_drift() -> None:
    eval_set = _eval_set("validation", ["v"])
    config = EvalConfig(metrics=[{"metric_name": "quality", "threshold": 0.5}], num_runs=1)
    raw = _raw(eval_set, {"v": [1]}, threshold=0.4)
    with pytest.raises(ValueError, match="threshold drift"):
        normalize_result(raw, eval_set, config, split=Split.VALIDATION, phase=Phase.BASELINE)


def test_comparison_classifies_all_transition_types() -> None:
    eval_set = _eval_set("validation", ["a", "b", "c", "d", "e"])
    config = EvalConfig(metrics=[{"metric_name": "quality", "threshold": 0.5}], num_runs=1)
    baseline = normalize_result(
        _raw(eval_set, {
            "a": [0],
            "b": [1],
            "c": [0.6],
            "d": [0.9],
            "e": [1]
        }),
        eval_set,
        config,
        split=Split.VALIDATION,
        phase=Phase.BASELINE,
    )
    candidate = normalize_result(
        _raw(eval_set, {
            "a": [1],
            "b": [0],
            "c": [0.8],
            "d": [0.7],
            "e": [1]
        }),
        eval_set,
        config,
        split=Split.VALIDATION,
        phase=Phase.CANDIDATE,
    )
    comparison = compare_snapshots(baseline, candidate, epsilon=1e-6, hard_case_ids=["b"])
    assert [case.transition for case in comparison.cases] == [
        Transition.NEW_PASS,
        Transition.NEW_FAIL,
        Transition.IMPROVED,
        Transition.REGRESSED,
        Transition.UNCHANGED,
    ]
    assert comparison.cases[1].new_hard_failure is True


def test_normalizer_does_not_mutate_sdk_result() -> None:
    eval_set = _eval_set("validation", ["v"])
    config = EvalConfig(metrics=[{"metric_name": "quality", "threshold": 0.5}], num_runs=1)
    raw = _raw(eval_set, {"v": [1]})
    before = deepcopy(raw.model_dump(mode="json"))
    normalize_result(raw, eval_set, config, split=Split.VALIDATION, phase=Phase.BASELINE)
    assert raw.model_dump(mode="json") == before
