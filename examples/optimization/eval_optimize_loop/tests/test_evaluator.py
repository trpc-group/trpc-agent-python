# -*- coding: utf-8 -*-
"""Tests for evaluation-result integrity checks."""

from types import SimpleNamespace

import pytest

from examples.optimization.eval_optimize_loop.pipeline.evaluator import (
    _validate_evaluation_completed,
)


def _result(*, status: str, score: float | None):
    metric = SimpleNamespace(
        metric_name="llm_rubric_response",
        eval_status=SimpleNamespace(name=status),
        score=score,
    )
    case_result = SimpleNamespace(overall_eval_metric_results=[metric])
    set_result = SimpleNamespace(
        eval_results_by_eval_id={"case_001": [case_result]}
    )
    return SimpleNamespace(results_by_eval_set_id={"eval_set": set_result})


def test_rejects_not_evaluated_metric():
    """API/judge failures must not be converted into ordinary zero scores."""
    with pytest.raises(RuntimeError, match="case_001.*NOT_EVALUATED"):
        _validate_evaluation_completed(
            _result(status="NOT_EVALUATED", score=None)
        )


def test_accepts_completed_metric():
    """A genuine zero is valid when the metric completed with FAILED."""
    _validate_evaluation_completed(_result(status="FAILED", score=0.0))
