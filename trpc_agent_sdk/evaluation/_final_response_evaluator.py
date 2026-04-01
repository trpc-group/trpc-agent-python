# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Final response evaluator (criterion-based)."""

from __future__ import annotations

import statistics
from typing import Any
from typing import Optional
from typing_extensions import override

from ._criterion_registry import CRITERION_REGISTRY
from ._eval_case import Invocation
from ._eval_criterion import FinalResponseCriterion
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_metrics import Interval
from ._eval_metrics import MetricInfo
from ._eval_metrics import MetricValueInfo
from ._eval_metrics import PrebuiltMetrics
from ._eval_result import EvaluationResult
from ._eval_result import PerInvocationResult
from ._evaluator_base import Evaluator


class FinalResponseEvaluator(Evaluator):
    """Compares final response per invocation.

    Uses FinalResponseCriterion (text/json) when criterion.finalResponse set;
    else exact text. Score 1.0 or 0.0 per invocation, overall = mean.
    """

    def __init__(
        self,
        threshold: Optional[float] = None,
        eval_metric: Optional[EvalMetric] = None,
    ):
        if threshold is not None and eval_metric:
            raise ValueError("Either eval_metric should be specified or threshold should be specified.")
        if eval_metric:
            threshold = eval_metric.threshold

        self._threshold = threshold
        self._criterion: Optional[Any] = None
        if eval_metric and eval_metric.criterion:
            self._criterion = CRITERION_REGISTRY.build(eval_metric.criterion, metric_key=eval_metric.metric_name)
        if self._criterion is None:
            self._criterion = FinalResponseCriterion.from_dict({"text": {"match": "exact"}})

    @staticmethod
    def get_metric_info() -> MetricInfo:
        return MetricInfo(
            metric_name=PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value,
            description=("Binary match of final response per invocation using "
                         "FinalResponseCriterion (text and/or json). Score 1.0 or 0.0."),
            metric_value_info=MetricValueInfo(interval=Interval(min_value=0.0, max_value=1.0)),
        )

    @override
    def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        if expected_invocations is None:
            raise ValueError("expected_invocations is required for final_response_avg_score")

        per_invocation_results = []
        for actual, expected in zip(actual_invocations, expected_invocations):
            match = self._criterion.matches(actual.final_response, expected.final_response)
            score = 1.0 if match else 0.0
            per_invocation_results.append(
                PerInvocationResult(
                    actual_invocation=actual,
                    expected_invocation=expected,
                    score=score,
                    eval_status=self._get_eval_status(score),
                ))

        if not per_invocation_results:
            return EvaluationResult()
        overall_score = statistics.mean([r.score for r in per_invocation_results])
        return EvaluationResult(
            overall_score=overall_score,
            overall_eval_status=self._get_eval_status(overall_score),
            per_invocation_results=per_invocation_results,
        )

    def _get_eval_status(self, score: float) -> EvalStatus:
        return EvalStatus.PASSED if score >= self._threshold else EvalStatus.FAILED
