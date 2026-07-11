from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.pipeline.gate import evaluate_gate, select_winner
from examples.optimization.eval_optimize_loop.pipeline.models import (
    CandidateReport,
    CaseDelta,
    CaseSnapshot,
    GateSettings,
    OptimizationReport,
    SplitReport,
)
from examples.optimization.eval_optimize_loop.run_pipeline import run_fake_pipeline, run_trace_pipeline


def _case(eval_id: str = "validation-1", *, passed: bool = True, score: float = 1.0, hard_failed: bool = False, metric: float = 1.0) -> CaseSnapshot:
    return CaseSnapshot(
        eval_id=eval_id, split="validation", run_count=1, passed=passed, hard_failed=hard_failed,
        aggregate_score=score, metric_scores={"quality": metric}, metric_thresholds={"quality": 0.0},
        metric_passed={"quality": metric >= 0.0}, trace_digest="sha256:test",
    )


def _delta(*, transition: str = "IMPROVED", hard_fail_added: bool = False, critical: bool = False) -> CaseDelta:
    return CaseDelta(
        eval_id="validation-1", baseline_passed=True, candidate_passed=transition != "REGRESSION",
        transition=transition, baseline_score=0.5, candidate_score=1.0 if transition != "REGRESSION" else 0.0,
        score_delta=0.5 if transition != "REGRESSION" else -0.5, metric_deltas={"quality": 0.5},
        critical=critical, hard_fail_added=hard_fail_added,
    )


def _decision(**kwargs):
    baseline = kwargs.pop("baseline", SplitReport.from_cases([_case(score=0.5, metric=0.5)]))
    candidate = kwargs.pop("candidate", SplitReport.from_cases([_case(score=1.0, metric=1.0)]))
    settings = GateSettings(min_validation_score_delta=0.0, **kwargs.pop("settings", {}))
    return evaluate_gate(baseline, candidate, settings=settings, case_deltas=kwargs.pop("case_deltas", [_delta()]), train_score_delta=kwargs.pop("train_score_delta", 0.1), **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "failed_rule"),
    [
        ({"candidate": SplitReport.from_cases([])}, "evaluation_complete"),
        ({"case_deltas": [_delta(hard_fail_added=True)]}, "new_hard_fails"),
        ({"case_deltas": [_delta(transition="REGRESSION", critical=True)]}, "no_critical_regression"),
        ({"case_deltas": [_delta(transition="REGRESSION")]}, "validation_regressions"),
        ({"metric_floors": {"quality": 1.1}}, "metric_floor:quality"),
        ({"train_score_delta": 0.1, "candidate": SplitReport.from_cases([_case(score=0.4)])}, "no_overfit"),
        ({"train_score_delta": 0.9, "settings": {"max_generalization_gap": 0.1}}, "generalization_gap"),
        ({"generation_cost_usd": 2.0, "settings": {"max_generation_cost_usd": 1.0}}, "generation_cost_budget"),
        ({"duration_seconds": 2.0, "settings": {"max_duration_seconds": 1.0}}, "duration_budget"),
    ],
)
def test_gate_matrix_rejects_each_hard_constraint(kwargs: dict[str, object], failed_rule: str) -> None:
    decision = _decision(**kwargs)
    assert decision.accepted is False
    assert next(rule for rule in decision.rules if rule.rule == failed_rule).passed is False


def test_gate_records_unknown_cost_warning_and_rejects_configured_tie() -> None:
    decision = _decision(generation_cost_usd=None)
    assert decision.accepted is True
    assert "generation cost is unknown" in decision.warnings
    baseline = SplitReport.from_cases([_case(score=1.0)])
    candidate = SplitReport.from_cases([_case(score=1.0)])
    tie = evaluate_gate(baseline, candidate, settings=GateSettings(min_validation_score_delta=0.0), case_deltas=[_delta(transition="UNCHANGED")])
    assert tie.accepted is False
    assert next(rule for rule in tie.rules if rule.rule == "tie_policy").passed is False


def test_winner_selection_is_stable_and_requires_independent_evaluation() -> None:
    train = SplitReport.from_cases([_case(eval_id="train-1")])
    validation = SplitReport.from_cases([_case()])
    accepted = CandidateReport(candidate_id="z", accepted=True, train=train, validation=validation, independently_evaluated=True, generation_cost_usd=1.0, duration_seconds=2.0)
    also_accepted = accepted.model_copy(update={"candidate_id": "a"})
    unverified = accepted.model_copy(update={"candidate_id": "unverified", "independently_evaluated": False})
    assert select_winner([accepted, also_accepted, unverified]) == "a"


@pytest.mark.asyncio
async def test_fake_report_round_trip_has_secret_free_audit_evidence(tmp_path: Path) -> None:
    report = await run_fake_pipeline(output_dir=tmp_path)
    loaded = OptimizationReport.model_validate_json((tmp_path / "optimization_report.json").read_text(encoding="utf-8"))
    assert loaded.selected_candidate_id == report.selected_candidate_id
    for path in loaded.audit_references.model_dump().values():
        assert (tmp_path / path).is_file()
    serialized = (tmp_path / "optimization_report.json").read_text(encoding="utf-8").lower()
    assert "unused-in-test" not in serialized
    assert "api_key" not in serialized


@pytest.mark.asyncio
async def test_fake_and_trace_outputs_are_deterministic_and_auditable(tmp_path: Path) -> None:
    first = await run_fake_pipeline(output_dir=tmp_path / "first")
    second = await run_fake_pipeline(output_dir=tmp_path / "second")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    trace = await run_trace_pipeline(output_dir=tmp_path / "trace")
    assert trace.audit_references.raw_reports_path == Path("audit/raw_reports.json")
    assert json.loads((tmp_path / "trace" / "audit" / "normalized_reports.json").read_text(encoding="utf-8"))
