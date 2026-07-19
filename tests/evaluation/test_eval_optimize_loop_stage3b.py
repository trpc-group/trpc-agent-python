# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Tests for stage 3b deterministic Gate decisions."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from statistics import mean

import pytest

from examples.optimization.eval_optimize_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.case_diff import compare_evaluations
from examples.optimization.eval_optimize_loop.config import BudgetConfig
from examples.optimization.eval_optimize_loop.config import GateConfig
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.pipeline import FakeStageExecutionError
from examples.optimization.eval_optimize_loop.pipeline import run_fake_stage
from examples.optimization.eval_optimize_loop.schemas import CaseEvaluation
from examples.optimization.eval_optimize_loop.schemas import EvaluationAnalysis
from examples.optimization.eval_optimize_loop.schemas import FakeStageResult
from examples.optimization.eval_optimize_loop.schemas import MetricOutcome
from examples.optimization.eval_optimize_loop.schemas import ObservableValue
from examples.optimization.eval_optimize_loop.schemas import ResourceMeasurements
from examples.optimization.eval_optimize_loop.schemas import StandardizedEvaluation


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target


def _available(value: float, unit: str | None = None) -> ObservableValue:
    return ObservableValue(status="available", value=value, unit=unit)


def _case(
    eval_id: str,
    *,
    status: str,
    score: float,
    metric_name: str = "final_response_avg_score",
) -> CaseEvaluation:
    return CaseEvaluation(
        eval_id=eval_id,
        status=status,
        average_score=_available(score),
        metrics=[
            MetricOutcome(
                metric_name=metric_name,
                threshold=0.5,
                status=status,
                score=_available(score),
            )
        ],
        runs=[],
    )


def _evaluation(
    phase: str,
    split: str,
    cases: list[CaseEvaluation],
) -> StandardizedEvaluation:
    return StandardizedEvaluation(
        phase=phase,
        split=split,
        eval_set_id=f"{split}-set",
        cases=cases,
        passed_case_count=sum(case.status == "passed" for case in cases),
        failed_case_count=sum(case.status == "failed" for case in cases),
        not_evaluated_case_count=sum(case.status == "not_evaluated" for case in cases),
        average_score=_available(mean(float(case.average_score.value) for case in cases)),
    )


def _analysis(
    *,
    baseline_train: list[CaseEvaluation] | None = None,
    candidate_train: list[CaseEvaluation] | None = None,
    baseline_validation: list[CaseEvaluation] | None = None,
    candidate_validation: list[CaseEvaluation] | None = None,
    hard_case_ids: set[str] | None = None,
    critical_case_ids: set[str] | None = None,
    overfit_status: str = "not_detected",
) -> EvaluationAnalysis:
    normalized_baseline_train = _evaluation(
        "baseline",
        "train",
        baseline_train or [_case("train_case", status="failed", score=0.0)],
    )
    normalized_candidate_train = _evaluation(
        "candidate",
        "train",
        candidate_train or [_case("train_case", status="passed", score=1.0)],
    )
    normalized_baseline_validation = _evaluation(
        "baseline",
        "validation",
        baseline_validation or [_case("validation_case", status="failed", score=0.0)],
    )
    normalized_candidate_validation = _evaluation(
        "candidate",
        "validation",
        candidate_validation or [_case("validation_case", status="passed", score=1.0)],
    )
    train_diff = compare_evaluations(
        normalized_baseline_train,
        normalized_candidate_train,
        hard_case_ids=hard_case_ids or set(),
        critical_case_ids=critical_case_ids or set(),
        severe_case_score_drop=0.2,
    )
    validation_diff = compare_evaluations(
        normalized_baseline_validation,
        normalized_candidate_validation,
        hard_case_ids=hard_case_ids or set(),
        critical_case_ids=critical_case_ids or set(),
        severe_case_score_drop=0.2,
    )
    return EvaluationAnalysis(
        baseline_train=normalized_baseline_train,
        baseline_validation=normalized_baseline_validation,
        candidate_train=normalized_candidate_train,
        candidate_validation=normalized_candidate_validation,
        train_diff=train_diff,
        validation_diff=validation_diff,
        overfit_status=overfit_status,
        overfit_reason="test overfit status",
    )


def _measurements() -> ResourceMeasurements:
    return ResourceMeasurements(
        cost_usd=ObservableValue(status="unavailable", reason="not observed"),
        total_tokens=ObservableValue(status="unavailable", reason="not observed"),
        duration_seconds=_available(1.0, "seconds"),
    )


def _rule(decision, rule_id: str):
    return next(result for result in decision.rule_results if result.rule_id == rule_id)


def test_gate_models_are_serializable_and_auditable():
    from examples.optimization.eval_optimize_loop.schemas import GateDecision
    from examples.optimization.eval_optimize_loop.schemas import GateRuleResult
    from examples.optimization.eval_optimize_loop.schemas import ObservableValue
    from examples.optimization.eval_optimize_loop.schemas import ResourceMeasurements

    measurements = ResourceMeasurements(
        cost_usd=ObservableValue(status="unavailable", reason="fake mode"),
        total_tokens=ObservableValue(status="available", value=12, unit="tokens"),
        duration_seconds=ObservableValue(status="available", value=0.5, unit="seconds"),
    )
    decision = GateDecision(
        decision="reject",
        rule_results=[
            GateRuleResult(
                rule_id="minimum_validation_score_delta",
                outcome="reject",
                message="Validation score improvement is below the configured minimum.",
                observed={"validation_score_delta": ObservableValue(status="available", value=0)},
                threshold=0.05,
            )
        ],
        rejection_reasons=["Validation score improvement is below the configured minimum."],
        warnings=[],
    )

    restored_measurements = ResourceMeasurements.model_validate_json(
        measurements.model_dump_json()
    )
    restored_decision = GateDecision.model_validate_json(decision.model_dump_json())

    assert restored_measurements == measurements
    assert restored_decision == decision
    assert restored_decision.rule_results[0].case_ids == []
    assert restored_decision.rule_results[0].metric_names == []


def test_gate_accepts_candidate_and_returns_every_rule_in_stable_order():
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    decision = evaluate_gate(
        _analysis(),
        GateConfig(),
        BudgetConfig(max_duration_seconds=10.0, on_unavailable="warning"),
        _measurements(),
    )

    assert decision.decision == "accept"
    assert decision.rejection_reasons == []
    assert decision.warnings == []
    assert [result.rule_id for result in decision.rule_results] == [
        "evaluation_completeness",
        "minimum_validation_score_delta",
        "validation_pass_rate_non_decrease",
        "no_new_hard_fail",
        "no_critical_regression",
        "no_severe_regression",
        "required_metrics",
        "no_overfitting",
        "cost_budget",
        "token_budget",
        "duration_budget",
    ]
    assert [result.outcome for result in decision.rule_results[-3:]] == [
        "skipped",
        "skipped",
        "pass",
    ]


def test_gate_rejects_incomplete_evaluation_and_unavailable_validation_delta():
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    analysis = _analysis()
    unavailable = ObservableValue(status="unavailable", reason="metric unavailable")
    candidate_case = analysis.candidate_validation.cases[0]
    incomplete_case = candidate_case.model_copy(
        update={
            "status": "not_evaluated",
            "average_score": unavailable,
            "metrics": [
                candidate_case.metrics[0].model_copy(
                    update={"status": "not_evaluated", "score": unavailable}
                )
            ],
        }
    )
    incomplete_candidate = analysis.candidate_validation.model_copy(
        update={
            "cases": [incomplete_case],
            "passed_case_count": 0,
            "not_evaluated_case_count": 1,
            "average_score": unavailable,
        }
    )
    incomplete_diff = analysis.validation_diff.model_copy(
        update={"candidate_average_score": unavailable, "score_delta": unavailable}
    )
    incomplete_analysis = analysis.model_copy(
        update={
            "candidate_validation": incomplete_candidate,
            "validation_diff": incomplete_diff,
        }
    )

    decision = evaluate_gate(
        incomplete_analysis,
        GateConfig(),
        BudgetConfig(),
        _measurements(),
    )

    assert decision.decision == "reject"
    assert _rule(decision, "evaluation_completeness").outcome == "reject"
    assert _rule(decision, "minimum_validation_score_delta").outcome == "reject"
    assert len(decision.rejection_reasons) >= 2


def test_minimum_validation_delta_accepts_exact_threshold():
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    analysis = _analysis(
        baseline_validation=[_case("validation_case", status="failed", score=0.0)],
        candidate_validation=[_case("validation_case", status="failed", score=0.05)],
    )

    decision = evaluate_gate(
        analysis,
        GateConfig(min_validation_score_delta=0.05),
        BudgetConfig(),
        _measurements(),
    )

    assert _rule(decision, "minimum_validation_score_delta").outcome == "pass"


def test_validation_pass_rate_drop_rejects_unless_rule_is_disabled():
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    analysis = _analysis(
        baseline_validation=[
            _case("validation_a", status="passed", score=1.0),
            _case("validation_b", status="failed", score=0.0),
        ],
        candidate_validation=[
            _case("validation_a", status="failed", score=0.0),
            _case("validation_b", status="failed", score=0.0),
        ],
    )

    rejected = evaluate_gate(analysis, GateConfig(), BudgetConfig(), _measurements())
    disabled = evaluate_gate(
        analysis,
        GateConfig(reject_on_validation_pass_rate_drop=False),
        BudgetConfig(),
        _measurements(),
    )

    result = _rule(rejected, "validation_pass_rate_non_decrease")
    assert result.outcome == "reject"
    assert result.observed["baseline_validation_pass_rate"].value == 0.5
    assert result.observed["candidate_validation_pass_rate"].value == 0.0
    assert _rule(disabled, "validation_pass_rate_non_decrease").outcome == "skipped"


def test_gate_collects_hard_critical_and_severe_train_regressions():
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    analysis = _analysis(
        baseline_train=[
            _case("hard_case", status="passed", score=1.0),
            _case("critical_case", status="passed", score=1.0),
            _case("severe_case", status="passed", score=1.0),
        ],
        candidate_train=[
            _case("hard_case", status="failed", score=0.0),
            _case("critical_case", status="passed", score=0.9),
            _case("severe_case", status="passed", score=0.7),
        ],
        hard_case_ids={"hard_case"},
        critical_case_ids={"critical_case"},
    )

    decision = evaluate_gate(analysis, GateConfig(), BudgetConfig(), _measurements())

    assert _rule(decision, "no_new_hard_fail").outcome == "reject"
    assert _rule(decision, "no_new_hard_fail").case_ids == ["hard_case"]
    assert _rule(decision, "no_critical_regression").outcome == "reject"
    assert _rule(decision, "no_critical_regression").case_ids == ["critical_case"]
    assert _rule(decision, "no_severe_regression").outcome == "reject"
    assert _rule(decision, "no_severe_regression").case_ids == ["hard_case", "severe_case"]

    disabled = evaluate_gate(
        analysis,
        GateConfig(reject_new_hard_fail=False, reject_critical_regression=False),
        BudgetConfig(),
        _measurements(),
    )
    assert _rule(disabled, "no_new_hard_fail").outcome == "skipped"
    assert _rule(disabled, "no_critical_regression").outcome == "skipped"
    assert _rule(disabled, "no_severe_regression").outcome == "reject"


@pytest.mark.parametrize("required_metrics", [["required_metric"], "all"])
def test_required_metrics_reject_missing_or_empty_candidate_metrics(required_metrics):
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    analysis = _analysis()
    if required_metrics == "all":
        empty_case = analysis.candidate_train.cases[0].model_copy(update={"metrics": []})
        analysis = analysis.model_copy(
            update={
                "candidate_train": analysis.candidate_train.model_copy(
                    update={"cases": [empty_case]}
                )
            }
        )

    decision = evaluate_gate(
        analysis,
        GateConfig(required_metrics=required_metrics),
        BudgetConfig(),
        _measurements(),
    )

    result = _rule(decision, "required_metrics")
    assert result.outcome == "reject"
    if required_metrics == ["required_metric"]:
        assert result.metric_names == ["required_metric"]
        assert result.case_ids == ["train_case", "validation_case"]
    else:
        assert result.case_ids == ["train_case"]


def test_required_metrics_reject_failed_and_unavailable_candidate_metrics():
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    failed_case = _case("train_case", status="failed", score=0.0)
    unavailable = ObservableValue(status="unavailable", reason="judge failed")
    unavailable_case = _case("validation_case", status="passed", score=1.0)
    unavailable_case = unavailable_case.model_copy(
        update={
            "metrics": [
                unavailable_case.metrics[0].model_copy(
                    update={"status": "not_evaluated", "score": unavailable}
                )
            ]
        }
    )
    analysis = _analysis(
        candidate_train=[failed_case],
        candidate_validation=[unavailable_case],
    )

    decision = evaluate_gate(analysis, GateConfig(), BudgetConfig(), _measurements())

    result = _rule(decision, "required_metrics")
    assert result.outcome == "reject"
    assert result.case_ids == ["train_case", "validation_case"]
    assert result.metric_names == ["final_response_avg_score"]


@pytest.mark.parametrize("overfit_status", ["detected", "unavailable"])
def test_overfit_detected_or_unavailable_rejects(overfit_status: str):
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    decision = evaluate_gate(
        _analysis(overfit_status=overfit_status),
        GateConfig(),
        BudgetConfig(),
        _measurements(),
    )

    assert _rule(decision, "no_overfitting").outcome == "reject"


def test_budget_rules_handle_limits_unavailable_policy_and_disabled_limits():
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    measurements = ResourceMeasurements(
        cost_usd=_available(2.0, "USD"),
        total_tokens=ObservableValue(status="unavailable", reason="usage missing"),
        duration_seconds=_available(1.0, "seconds"),
    )
    warning_decision = evaluate_gate(
        _analysis(),
        GateConfig(),
        BudgetConfig(max_cost_usd=1.0, max_tokens=100, on_unavailable="warning"),
        measurements,
    )

    assert _rule(warning_decision, "cost_budget").outcome == "reject"
    assert _rule(warning_decision, "token_budget").outcome == "warning"
    assert _rule(warning_decision, "duration_budget").outcome == "skipped"
    assert len(warning_decision.warnings) == 1

    reject_decision = evaluate_gate(
        _analysis(),
        GateConfig(),
        BudgetConfig(max_tokens=100, max_duration_seconds=1.0, on_unavailable="reject"),
        measurements,
    )
    assert _rule(reject_decision, "cost_budget").outcome == "skipped"
    assert _rule(reject_decision, "token_budget").outcome == "reject"
    assert _rule(reject_decision, "duration_budget").outcome == "pass"


def test_gate_rejects_structurally_inconsistent_analysis():
    from examples.optimization.eval_optimize_loop.gate import GateEvaluationError
    from examples.optimization.eval_optimize_loop.gate import evaluate_gate

    analysis = _analysis()
    wrong_split = analysis.model_copy(
        update={"train_diff": analysis.train_diff.model_copy(update={"split": "validation"})}
    )
    with pytest.raises(GateEvaluationError, match="train_diff.split"):
        evaluate_gate(wrong_split, GateConfig(), BudgetConfig(), _measurements())

    missing_diff_case = analysis.model_copy(
        update={
            "train_diff": analysis.train_diff.model_copy(update={"cases": []}),
        }
    )
    with pytest.raises(GateEvaluationError, match="case ids"):
        evaluate_gate(missing_diff_case, GateConfig(), BudgetConfig(), _measurements())

    candidate_case = analysis.candidate_train.cases[0]
    duplicate_metric_case = candidate_case.model_copy(
        update={"metrics": [candidate_case.metrics[0], candidate_case.metrics[0]]}
    )
    duplicate_metric_analysis = analysis.model_copy(
        update={
            "candidate_train": analysis.candidate_train.model_copy(
                update={"cases": [duplicate_metric_case]}
            )
        }
    )
    with pytest.raises(GateEvaluationError, match="duplicate metric"):
        evaluate_gate(
            duplicate_metric_analysis,
            GateConfig(),
            BudgetConfig(),
            _measurements(),
        )


@pytest.mark.asyncio
async def test_fake_stage_returns_measurements_and_accepts_improving_candidate(tmp_path: Path):
    root = _copy_example(tmp_path, "improve")
    source = root / "prompts" / "system.md"
    baseline_source = source.read_text(encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id="stage3b_improve")

    result = await run_fake_stage(prepared, scenario="improve")

    assert result.gate_decision.decision == "accept"
    assert result.gate_decision.rejection_reasons == []
    assert result.measurements.duration_seconds.status == "available"
    assert result.measurements.duration_seconds.value >= 0
    assert result.measurements.cost_usd.status == "unavailable"
    assert result.measurements.total_tokens.status == "unavailable"
    assert source.read_text(encoding="utf-8") == baseline_source
    assert "deterministic-fake-candidate:start" in (
        await prepared.working_target.read_all()
    )["system_prompt"]


@pytest.mark.parametrize(
    ("scenario", "expected_decision", "expected_reject_rules"),
    [
        ("improve", "accept", []),
        (
            "no_improvement",
            "reject",
            ["minimum_validation_score_delta", "required_metrics"],
        ),
        (
            "overfit",
            "reject",
            [
                "minimum_validation_score_delta",
                "validation_pass_rate_non_decrease",
                "no_critical_regression",
                "no_severe_regression",
                "required_metrics",
                "no_overfitting",
            ],
        ),
    ],
)
@pytest.mark.asyncio
async def test_stage3b_scenario_gate_matrix(
    tmp_path: Path,
    scenario: str,
    expected_decision: str,
    expected_reject_rules: list[str],
):
    root = _copy_example(tmp_path, f"matrix_{scenario}")
    source = root / "prompts" / "system.md"
    baseline_source = source.read_text(encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id=f"stage3b_{scenario}")

    result = await run_fake_stage(prepared, scenario=scenario)  # type: ignore[arg-type]

    assert result.gate_decision.decision == expected_decision
    assert [
        rule.rule_id
        for rule in result.gate_decision.rule_results
        if rule.outcome == "reject"
    ] == expected_reject_rules
    assert source.read_text(encoding="utf-8") == baseline_source
    assert FakeStageResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.asyncio
async def test_fake_stage_wraps_gate_structure_errors_only(tmp_path: Path, monkeypatch):
    from examples.optimization.eval_optimize_loop.gate import GateEvaluationError

    root = _copy_example(tmp_path, "gate_structure_error")
    prepared = prepare_run(root / "pipeline.json", run_id="gate_structure_error")

    def fail_gate(*_args, **_kwargs):
        raise GateEvaluationError("injected unsafe analysis")

    monkeypatch.setattr(pipeline_module, "evaluate_gate", fail_gate)

    with pytest.raises(FakeStageExecutionError, match="stage 3b gate failed"):
        await run_fake_stage(prepared, scenario="improve")


@pytest.mark.asyncio
async def test_fake_stage_does_not_hide_unexpected_gate_value_errors(tmp_path: Path, monkeypatch):
    root = _copy_example(tmp_path, "gate_programming_error")
    prepared = prepare_run(root / "pipeline.json", run_id="gate_programming_error")

    def fail_gate(*_args, **_kwargs):
        raise ValueError("injected programming error")

    monkeypatch.setattr(pipeline_module, "evaluate_gate", fail_gate)

    with pytest.raises(ValueError, match="injected programming error"):
        await run_fake_stage(prepared, scenario="improve")


def test_stage3b_cli_prints_gate_decision_and_rejection_reasons(tmp_path: Path):
    root = _copy_example(tmp_path, "cli_overfit")

    completed = subprocess.run(
        [
            sys.executable,
            str(_EXAMPLE / "run_pipeline.py"),
            "--config",
            str(root / "pipeline.json"),
            "--run-id",
            "stage3b_cli",
            "--scenario",
            "overfit",
        ],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert "Gate decision: REJECT" in completed.stdout
    assert "Rejection reasons:" in completed.stdout
    assert "[no_overfitting]" in completed.stdout
    assert "Writeback: SKIPPED (gate_rejected)" in completed.stdout
    assert "optimization_report.json" in completed.stdout
    assert "optimization_report.md" in completed.stdout
    assert "artifact_index.json" in completed.stdout
