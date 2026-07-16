from __future__ import annotations

import pytest

from ..delta import compute_delta
from ..models import PerCaseResult, SplitResult, SplitDelta, PerCaseDelta


def test_compute_delta_all_passing():
    baseline = {
        "train": SplitResult(
            pass_rate=1.0,
            metric_breakdown={"m1": 1.0},
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
                "c2": PerCaseResult(case_id="c2", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=1.0,
            metric_breakdown={"m1": 1.0},
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
                "c2": PerCaseResult(case_id="c2", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
    }
    candidate = {
        "train": SplitResult(
            pass_rate=1.0,
            metric_breakdown={"m1": 1.0},
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
                "c2": PerCaseResult(case_id="c2", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=1.0,
            metric_breakdown={"m1": 1.0},
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
                "c2": PerCaseResult(case_id="c2", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
    }
    delta = compute_delta(baseline, candidate)
    assert isinstance(delta, SplitDelta)
    assert delta.train_pass_rate_delta == 0.0
    assert delta.val_pass_rate_delta == 0.0
    assert len(delta.train.newly_passing) == 0
    assert len(delta.train.newly_failing) == 0
    assert len(delta.val.unchanged) == 2


def test_compute_delta_improvement():
    baseline = {
        "train": SplitResult(
            pass_rate=0.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=False, metric_scores={"m1": 0.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=0.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=False, metric_scores={"m1": 0.0}),
            },
        ),
    }
    candidate = {
        "train": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
    }
    delta = compute_delta(baseline, candidate)
    assert delta.train_pass_rate_delta == 1.0
    assert delta.val_pass_rate_delta == 1.0
    assert delta.train.newly_passing == ["c1"]
    assert delta.train.newly_failing == []
    assert delta.val.newly_passing == ["c1"]


def test_compute_delta_regression():
    baseline = {
        "train": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
    }
    candidate = {
        "train": SplitResult(
            pass_rate=0.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=False, metric_scores={"m1": 0.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=0.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=False, metric_scores={"m1": 0.0}),
            },
        ),
    }
    delta = compute_delta(baseline, candidate)
    assert delta.train_pass_rate_delta == -1.0
    assert delta.train.newly_failing == ["c1"]
    assert delta.val.newly_failing == ["c1"]


def test_compute_delta_mixed():
    """Train improves but val degrades (overfitting scenario)."""
    baseline = {
        "train": SplitResult(
            pass_rate=0.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=False, metric_scores={"m1": 0.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
    }
    candidate = {
        "train": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 1.0}),
            },
        ),
        "val": SplitResult(
            pass_rate=0.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=False, metric_scores={"m1": 0.0}),
            },
        ),
    }
    delta = compute_delta(baseline, candidate)
    assert delta.train_pass_rate_delta == 1.0
    assert delta.val_pass_rate_delta == -1.0
    assert delta.train.newly_passing == ["c1"]
    assert delta.val.newly_failing == ["c1"]


def test_compute_delta_score_deltas():
    baseline = {
        "train": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 0.8, "m2": 0.9}),
            },
        ),
        "val": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 0.8, "m2": 0.9}),
            },
        ),
    }
    candidate = {
        "train": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 0.9, "m2": 0.7}),
            },
        ),
        "val": SplitResult(
            pass_rate=1.0,
            per_case={
                "c1": PerCaseResult(case_id="c1", passed=True, metric_scores={"m1": 0.9, "m2": 0.7}),
            },
        ),
    }
    delta = compute_delta(baseline, candidate)
    assert delta.train.score_deltas["c1"]["m1"] == pytest.approx(0.1)
    assert delta.train.score_deltas["c1"]["m2"] == pytest.approx(-0.2)
    assert delta.val.score_deltas["c1"]["m1"] == pytest.approx(0.1)
    assert delta.val.score_deltas["c1"]["m2"] == pytest.approx(-0.2)
