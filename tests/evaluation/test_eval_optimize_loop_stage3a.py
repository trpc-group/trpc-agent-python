# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Tests for stage 3a evaluation normalization, attribution, and case diff."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.attribution import attribute_evaluation
from examples.optimization.eval_optimize_loop.case_diff import compare_evaluations
from examples.optimization.eval_optimize_loop.evaluation_adapter import EvaluationAnalysisError
from examples.optimization.eval_optimize_loop.evaluation_adapter import standardize_snapshot
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.pipeline import run_fake_stage
from examples.optimization.eval_optimize_loop.schemas import FakeEvaluationSnapshot
from examples.optimization.eval_optimize_loop.schemas import FakeStageResult
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalMetricResultDetails
from trpc_agent_sdk.evaluation import EvalMetricResultPerInvocation
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import IntermediateData
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target


def _content(text: str) -> Content:
    return Content(parts=[Part(text=text)])


def _metric(
    *,
    score: float | None,
    status: EvalStatus,
    name: str = "final_response_avg_score",
    threshold: float = 1.0,
    reason: str | None = None,
) -> EvalMetricResult:
    return EvalMetricResult(
        metric_name=name,
        threshold=threshold,
        score=score,
        eval_status=status,
        details=EvalMetricResultDetails(reason=reason, score=score) if reason else None,
    )


def _case_run(
    *,
    run_id: int,
    status: EvalStatus,
    score: float | None,
    actual_text: str = '{"route":"general_support"}',
    expected_text: str = '{"route":"order_lookup"}',
    actual_tools: list[FunctionCall] | None = None,
    expected_tools: list[FunctionCall] | None = None,
    error_message: str | None = None,
    metrics: list[EvalMetricResult] | None = None,
) -> EvalCaseResult:
    actual = Invocation(
        invocation_id="inv-1",
        user_content=_content("Check order A100"),
        final_response=_content(actual_text),
        intermediate_data=IntermediateData(tool_uses=actual_tools or []),
    )
    expected = Invocation(
        invocation_id="inv-1",
        user_content=_content("Check order A100"),
        final_response=_content(expected_text),
        intermediate_data=IntermediateData(tool_uses=expected_tools or []),
    )
    metric_results = metrics or [_metric(score=score, status=status)]
    return EvalCaseResult(
        eval_set_id="train-set",
        eval_id="case-order",
        run_id=run_id,
        final_eval_status=status,
        error_message=error_message,
        overall_eval_metric_results=metric_results,
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=actual,
                expected_invocation=expected,
                eval_metric_results=metric_results,
            )
        ],
        session_id=f"session-{run_id}",
    )


def _snapshot(*runs: EvalCaseResult) -> FakeEvaluationSnapshot:
    return FakeEvaluationSnapshot(
        phase="baseline",
        split="train",
        eval_set_id="train-set",
        details_lines=[],
        result_lines=[],
        eval_results_by_eval_id={"case-order": list(runs)},
        passed_case_count=0,
        total_case_count=1,
        average_score=0.5,
    )


def test_standardize_snapshot_preserves_multirun_metric_and_invocation_evidence():
    expected_tool = FunctionCall(name="lookup_order", args={"order_id": "A100"})
    actual_tool = FunctionCall(name="general_support", args={})
    snapshot = _snapshot(
        _case_run(
            run_id=2,
            status=EvalStatus.FAILED,
            score=0.0,
            actual_tools=[actual_tool],
            expected_tools=[expected_tool],
        ),
        _case_run(
            run_id=1,
            status=EvalStatus.PASSED,
            score=1.0,
            actual_text='{"route":"order_lookup"}',
            actual_tools=[expected_tool],
            expected_tools=[expected_tool],
        ),
    )

    standardized = standardize_snapshot(snapshot)

    case = standardized.cases[0]
    assert case.eval_id == "case-order"
    assert case.status == "failed"
    assert case.average_score.status == "available"
    assert case.average_score.value == pytest.approx(0.5)
    assert case.metrics[0].metric_name == "final_response_avg_score"
    assert case.metrics[0].score.value == pytest.approx(0.5)
    assert [run.run_id for run in case.runs] == [1, 2]
    failed_evidence = case.runs[1].invocations[0]
    assert failed_evidence.user_text == "Check order A100"
    assert failed_evidence.actual_response == '{"route":"general_support"}'
    assert failed_evidence.expected_response == '{"route":"order_lookup"}'
    assert failed_evidence.actual_tools[0].name == "general_support"
    assert failed_evidence.expected_tools[0].arguments == {"order_id": "A100"}
    assert standardized.average_score.value == pytest.approx(0.5)


def test_standardize_snapshot_keeps_missing_scores_unavailable_instead_of_zero():
    standardized = standardize_snapshot(
        _snapshot(
            _case_run(
                run_id=1,
                status=EvalStatus.NOT_EVALUATED,
                score=None,
                error_message="metric failed",
            )
        )
    )

    case = standardized.cases[0]
    assert case.status == "not_evaluated"
    assert case.average_score.status == "unavailable"
    assert case.average_score.value is None
    assert case.metrics[0].score.status == "unavailable"
    assert standardized.average_score.status == "unavailable"
    assert standardized.not_evaluated_case_count == 1


def test_standardize_snapshot_treats_failed_metric_without_score_as_not_evaluated():
    standardized = standardize_snapshot(
        _snapshot(
            _case_run(
                run_id=1,
                status=EvalStatus.FAILED,
                score=None,
            )
        )
    )

    case = standardized.cases[0]
    assert case.status == "not_evaluated"
    assert case.runs[0].status == "not_evaluated"
    assert case.runs[0].metrics[0].status == "not_evaluated"


def test_standardize_snapshot_rejects_duplicate_run_ids_and_metric_threshold_drift():
    duplicate_runs = _snapshot(
        _case_run(run_id=1, status=EvalStatus.PASSED, score=1.0),
        _case_run(run_id=1, status=EvalStatus.PASSED, score=1.0),
    )
    with pytest.raises(EvaluationAnalysisError, match="duplicate run ids"):
        standardize_snapshot(duplicate_runs)

    threshold_drift = _snapshot(
        _case_run(run_id=1, status=EvalStatus.PASSED, score=1.0),
        _case_run(
            run_id=2,
            status=EvalStatus.PASSED,
            score=1.0,
            metrics=[_metric(score=1.0, status=EvalStatus.PASSED, threshold=0.5)],
        ),
    )
    with pytest.raises(EvaluationAnalysisError, match="inconsistent thresholds"):
        standardize_snapshot(threshold_drift)


def test_standardize_snapshot_rejects_result_from_a_different_evalset():
    wrong_evalset_run = _case_run(
        run_id=1,
        status=EvalStatus.PASSED,
        score=1.0,
    ).model_copy(update={"eval_set_id": "other-set"})

    with pytest.raises(EvaluationAnalysisError, match="eval_set_id"):
        standardize_snapshot(_snapshot(wrong_evalset_run))


def test_attribution_prefers_tool_name_error_and_retains_secondary_response_evidence():
    standardized = standardize_snapshot(
        _snapshot(
            _case_run(
                run_id=1,
                status=EvalStatus.FAILED,
                score=0.0,
                actual_tools=[FunctionCall(name="general_support", args={})],
                expected_tools=[FunctionCall(name="lookup_order", args={"order_id": "A100"})],
            )
        )
    )

    attributed = attribute_evaluation(standardized)

    attribution = attributed.cases[0].attribution
    assert attribution is not None
    assert attribution.primary_category == "tool_name_error"
    assert attribution.secondary_categories == ["routing_error", "final_response_mismatch"]
    assert attribution.summary == "Expected tool names ['lookup_order'], got ['general_support']."
    assert attribution.evidence[0].expected == ["lookup_order"]
    assert attribution.evidence[0].actual == ["general_support"]


@pytest.mark.parametrize(
    ("case_run", "expected_category"),
    [
        (
            _case_run(
                run_id=1,
                status=EvalStatus.NOT_EVALUATED,
                score=None,
                error_message="judge unavailable",
            ),
            "evaluation_error",
        ),
        (
            _case_run(
                run_id=1,
                status=EvalStatus.FAILED,
                score=0.0,
                actual_tools=[FunctionCall(name="lookup_order", args={"order_id": "B200"})],
                expected_tools=[FunctionCall(name="lookup_order", args={"order_id": "A100"})],
            ),
            "tool_argument_error",
        ),
        (
            _case_run(
                run_id=1,
                status=EvalStatus.FAILED,
                score=0.0,
                metrics=[
                    _metric(
                        name="llm_rubric_knowledge_recall",
                        score=0.0,
                        status=EvalStatus.FAILED,
                        reason="Shipping policy was not recalled.",
                    )
                ],
            ),
            "knowledge_recall",
        ),
        (
            _case_run(
                run_id=1,
                status=EvalStatus.FAILED,
                score=0.0,
                actual_text="not-json",
            ),
            "format_error",
        ),
        (
            _case_run(
                run_id=1,
                status=EvalStatus.FAILED,
                score=0.0,
                actual_text="same",
                expected_text="same",
                metrics=[
                    _metric(
                        name="llm_rubric_response",
                        score=0.0,
                        status=EvalStatus.FAILED,
                        reason="Rubric requirement was not met.",
                    )
                ],
            ),
            "rubric_failure",
        ),
        (
            _case_run(
                run_id=1,
                status=EvalStatus.FAILED,
                score=0.0,
                actual_text="same",
                expected_text="same",
                metrics=[_metric(name="custom_metric", score=0.0, status=EvalStatus.FAILED)],
            ),
            "unknown",
        ),
    ],
)
def test_attribution_uses_generic_evidence_categories(
    case_run: EvalCaseResult,
    expected_category: str,
):
    attributed = attribute_evaluation(standardize_snapshot(_snapshot(case_run)))

    attribution = attributed.cases[0].attribution
    assert attribution is not None
    assert attribution.primary_category == expected_category


def test_attribution_is_absent_for_passed_case():
    attributed = attribute_evaluation(
        standardize_snapshot(
            _snapshot(
                _case_run(
                    run_id=1,
                    status=EvalStatus.PASSED,
                    score=1.0,
                    actual_text='{"route":"order_lookup"}',
                )
            )
        )
    )

    assert attributed.cases[0].attribution is None


def test_compare_evaluations_marks_newly_passed_score_delta_and_hard_label():
    baseline = attribute_evaluation(
        standardize_snapshot(
            _snapshot(_case_run(run_id=1, status=EvalStatus.FAILED, score=0.0))
        )
    )
    candidate_snapshot = _snapshot(
        _case_run(
            run_id=1,
            status=EvalStatus.PASSED,
            score=1.0,
            actual_text='{"route":"order_lookup"}',
        )
    ).model_copy(update={"phase": "candidate", "average_score": 1.0})
    candidate = attribute_evaluation(standardize_snapshot(candidate_snapshot))

    diff = compare_evaluations(
        baseline,
        candidate,
        hard_case_ids={"case-order"},
        critical_case_ids=set(),
        severe_case_score_drop=0.2,
    )

    case_diff = diff.cases[0]
    assert case_diff.change == "newly_passed"
    assert case_diff.score_delta.status == "available"
    assert case_diff.score_delta.value == pytest.approx(1.0)
    assert case_diff.is_hard is True
    assert case_diff.is_critical is False
    assert case_diff.severe_regression is False
    assert diff.newly_passed_count == 1


def test_compare_evaluations_marks_critical_severe_regression_and_unavailable_as_incomparable():
    passing_snapshot = _snapshot(
        _case_run(
            run_id=1,
            status=EvalStatus.PASSED,
            score=1.0,
            actual_text='{"route":"order_lookup"}',
        )
    )
    baseline = attribute_evaluation(standardize_snapshot(passing_snapshot))
    failing_snapshot = _snapshot(_case_run(run_id=1, status=EvalStatus.FAILED, score=0.0)).model_copy(
        update={"phase": "candidate", "average_score": 0.0}
    )
    candidate = attribute_evaluation(standardize_snapshot(failing_snapshot))

    regression = compare_evaluations(
        baseline,
        candidate,
        hard_case_ids=set(),
        critical_case_ids={"case-order"},
        severe_case_score_drop=0.2,
    ).cases[0]
    assert regression.change == "newly_failed"
    assert regression.is_critical is True
    assert regression.severe_regression is True

    unavailable_snapshot = _snapshot(
        _case_run(
            run_id=1,
            status=EvalStatus.NOT_EVALUATED,
            score=None,
            error_message="candidate metric unavailable",
        )
    ).model_copy(update={"phase": "candidate", "average_score": None})
    unavailable = attribute_evaluation(standardize_snapshot(unavailable_snapshot))
    incomparable = compare_evaluations(
        baseline,
        unavailable,
        hard_case_ids=set(),
        critical_case_ids=set(),
        severe_case_score_drop=0.2,
    ).cases[0]
    assert incomparable.change == "incomparable"
    assert incomparable.score_delta.status == "unavailable"
    assert incomparable.severe_regression is False


def test_compare_evaluations_rejects_case_and_metric_threshold_mismatch():
    baseline = attribute_evaluation(
        standardize_snapshot(
            _snapshot(_case_run(run_id=1, status=EvalStatus.FAILED, score=0.0))
        )
    )
    candidate = attribute_evaluation(
        standardize_snapshot(
            _snapshot(
                _case_run(
                    run_id=1,
                    status=EvalStatus.PASSED,
                    score=1.0,
                    metrics=[_metric(score=1.0, status=EvalStatus.PASSED, threshold=0.5)],
                )
            ).model_copy(update={"phase": "candidate"})
        )
    )
    with pytest.raises(EvaluationAnalysisError, match="threshold changed"):
        compare_evaluations(
            baseline,
            candidate,
            hard_case_ids=set(),
            critical_case_ids=set(),
            severe_case_score_drop=0.2,
        )

    different_case = candidate.model_copy(
        update={"cases": [candidate.cases[0].model_copy(update={"eval_id": "other-case"})]}
    )
    with pytest.raises(EvaluationAnalysisError, match="case ids do not match"):
        compare_evaluations(
            baseline,
            different_case,
            hard_case_ids=set(),
            critical_case_ids=set(),
            severe_case_score_drop=0.2,
        )


@pytest.mark.parametrize(
    (
        "scenario",
        "train_newly_passed",
        "validation_newly_passed",
        "validation_newly_failed",
        "validation_unchanged",
        "overfit_status",
    ),
    [
        ("improve", 2, 2, 0, 1, "not_detected"),
        ("no_improvement", 0, 0, 0, 3, "not_detected"),
        ("overfit", 2, 0, 1, 2, "detected"),
    ],
)
@pytest.mark.asyncio
async def test_stage3a_scenario_analysis_matrix(
    tmp_path: Path,
    scenario: str,
    train_newly_passed: int,
    validation_newly_passed: int,
    validation_newly_failed: int,
    validation_unchanged: int,
    overfit_status: str,
):
    root = _copy_example(tmp_path, scenario)
    prepared = prepare_run(root / "pipeline.json", run_id=f"stage3a_{scenario}")

    result = await run_fake_stage(prepared, scenario=scenario)  # type: ignore[arg-type]

    analysis = result.analysis
    assert analysis.train_diff.newly_passed_count == train_newly_passed
    assert analysis.validation_diff.newly_passed_count == validation_newly_passed
    assert analysis.validation_diff.newly_failed_count == validation_newly_failed
    assert analysis.validation_diff.unchanged_count == validation_unchanged
    assert analysis.overfit_status == overfit_status

    if scenario == "overfit":
        refund = next(
            case for case in analysis.validation_diff.cases if case.eval_id == "val_refund_route"
        )
        assert refund.change == "newly_failed"
        assert refund.is_critical is True
        assert refund.severe_regression is True
        assert refund.candidate_attribution is not None
        assert refund.candidate_attribution.primary_category == "routing_error"

    restored = FakeStageResult.model_validate_json(result.model_dump_json())
    assert restored.analysis == analysis


@pytest.mark.asyncio
async def test_stage3a_does_not_hide_unexpected_value_errors(tmp_path: Path, monkeypatch):
    root = _copy_example(tmp_path, "unexpected_analysis_error")
    prepared = prepare_run(root / "pipeline.json", run_id="unexpected_analysis_error")

    def fail_analysis(**_kwargs):
        raise ValueError("injected programming error")

    monkeypatch.setattr(pipeline_module, "build_evaluation_analysis", fail_analysis)

    with pytest.raises(ValueError, match="injected programming error"):
        await run_fake_stage(prepared, scenario="improve")
