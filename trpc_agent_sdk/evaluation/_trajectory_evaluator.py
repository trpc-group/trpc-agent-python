# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Trajectory Evaluator for Tool Call Verification.

Evaluates agent tool call trajectories against expected sequences.
"""

from __future__ import annotations

import statistics
from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.types import FunctionCall

from ._criterion_registry import CRITERION_REGISTRY
from ._eval_case import Invocation
from ._eval_case import get_all_tool_calls
from ._eval_criterion import ToolTrajectoryCriterion
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_metrics import Interval
from ._eval_metrics import MetricInfo
from ._eval_metrics import MetricValueInfo
from ._eval_metrics import PrebuiltMetrics
from ._eval_result import EvaluationResult
from ._eval_result import PerInvocationResult
from ._evaluator_base import Evaluator


class TrajectoryEvaluator(Evaluator):
    """Compares tool call sequences.

    With criterion: ToolTrajectoryCriterion (order, subset, per-tool strategy).
    Without: strict count, order, name and arguments match.
    """

    def __init__(
        self,
        threshold: Optional[float] = None,
        eval_metric: Optional[EvalMetric] = None,
    ):
        if threshold is not None and eval_metric:
            raise ValueError("Either eval_metric should be specified or threshold should be "
                             "specified.")

        if eval_metric:
            threshold = eval_metric.threshold

        self._threshold = threshold
        self._trajectory_criterion: Optional[Any] = None
        if eval_metric and eval_metric.criterion:
            self._trajectory_criterion = CRITERION_REGISTRY.build(eval_metric.criterion,
                                                                  metric_key=eval_metric.metric_name)

    @staticmethod
    def get_metric_info() -> MetricInfo:
        return MetricInfo(
            metric_name=PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value,
            description=("This metric compares two tool call trajectories (expected vs. "
                         "actual) for the same user interaction. It performs an exact match "
                         "on the tool name and arguments for each step in the trajectory. "
                         "A score of 1.0 indicates a perfect match, while 0.0 indicates a "
                         "mismatch. Higher values are better."),
            metric_value_info=MetricValueInfo(interval=Interval(min_value=0.0, max_value=1.0)),
        )

    @override
    def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        """Compare tool call sequences per invocation.

        Return 1.0/0.0 per turn, mean overall.
        """
        if expected_invocations is None:
            raise ValueError("expected_invocations is required for trajectory evaluation")

        per_invocation_results = []

        for actual, expected in zip(actual_invocations, expected_invocations):
            actual_tool_calls = get_all_tool_calls(actual.intermediate_data)
            expected_tool_calls = get_all_tool_calls(expected.intermediate_data)

            if self._trajectory_criterion is not None:
                is_equal = self._trajectory_criterion.matches(actual_tool_calls, expected_tool_calls)
            else:
                is_equal = self._are_tool_calls_equal(actual_tool_calls, expected_tool_calls)
            score = 1.0 if is_equal else 0.0

            per_invocation_results.append(
                PerInvocationResult(actual_invocation=actual,
                                    expected_invocation=expected,
                                    score=score,
                                    eval_status=self._get_eval_status(score)))

        if per_invocation_results:
            overall_score = statistics.mean([r.score for r in per_invocation_results])

            return EvaluationResult(
                overall_score=overall_score,
                overall_eval_status=self._get_eval_status(overall_score),
                per_invocation_results=per_invocation_results,
            )

        return EvaluationResult()

    def _are_tool_calls_equal(
        self,
        actual_tool_calls: list[FunctionCall],
        expected_tool_calls: list[FunctionCall],
    ) -> bool:
        """True if same length and each pair has same name and args."""
        if len(actual_tool_calls) != len(expected_tool_calls):
            return False

        for actual, expected in zip(actual_tool_calls, expected_tool_calls):
            if actual.name != expected.name or actual.args != expected.args:
                return False

        return True

    def _get_eval_status(self, score: float) -> EvalStatus:
        return EvalStatus.PASSED if score >= self._threshold else EvalStatus.FAILED
