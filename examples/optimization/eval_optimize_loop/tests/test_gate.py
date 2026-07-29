# -*- coding: utf-8 -*-
"""Pure-function tests for the acceptance Gate."""

from examples.optimization.eval_optimize_loop.pipeline.config import (
    BaselineResult,
    CaseDelta,
    DeltaReport,
    GateConfig,
)
from examples.optimization.eval_optimize_loop.pipeline.gate import decide


BASELINE = BaselineResult(
    eval_set_id="val",
    pass_rate=0.5,
    metric_breakdown={},
)


def _delta(value: float, per_case=None) -> DeltaReport:
    return DeltaReport(
        baseline_pass_rate=0.5,
        candidate_pass_rate=0.5 + value,
        delta=value,
        per_case=per_case or [],
    )


def test_rejects_insufficient_improvement():
    decision = decide(BASELINE, _delta(0.05), GateConfig())
    assert decision.accepted is False
    assert decision.checks["min_improvement"] is False


def test_rejects_newly_failing_case():
    regression = CaseDelta(
        case_id="val_003",
        baseline_status="PASSED",
        candidate_status="FAILED",
        change_type="newly_failing",
    )
    decision = decide(BASELINE, _delta(0.2, [regression]), GateConfig())
    assert decision.accepted is False
    assert decision.checks["no_hard_regression"] is False


def test_rejects_train_gain_with_validation_loss_as_overfit():
    decision = decide(
        BASELINE,
        _delta(-0.2),
        GateConfig(),
        delta_report_train=_delta(0.2),
    )
    assert decision.accepted is False
    assert decision.checks["no_overfit"] is False


def test_accepts_when_all_checks_pass():
    decision = decide(
        BASELINE,
        _delta(0.2),
        GateConfig(),
        delta_report_train=_delta(0.2),
    )
    assert decision.accepted is True
    assert all(decision.checks.values())
