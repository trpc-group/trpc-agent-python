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
"""Rouge score evaluator for response matching."""

from __future__ import annotations

import statistics
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.types import Content

from ._eval_case import Invocation
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_metrics import Interval
from ._eval_metrics import MetricInfo
from ._eval_metrics import MetricValueInfo
from ._eval_metrics import PrebuiltMetrics
from ._eval_result import EvaluationResult
from ._eval_result import PerInvocationResult
from ._evaluator_base import Evaluator


class RougeEvaluator(Evaluator):
    """Evaluates response matching using Rouge-1 metric.

    Rouge-1 measures the overlap of unigrams (single words) between
    the actual and expected responses.

    Score range: [0, 1], where 1 means perfect match.
    """

    def __init__(
        self,
        threshold: Optional[float] = None,
        eval_metric: Optional[EvalMetric] = None,
    ):
        """Initialize the evaluator.

        Args:
            threshold: Threshold value (deprecated, use eval_metric instead)
            eval_metric: The metric configuration

        Raises:
            ValueError: If both threshold and eval_metric are specified
        """
        if threshold is not None and eval_metric:
            raise ValueError("Either eval_metric should be specified or threshold should be "
                             "specified.")

        if eval_metric:
            threshold = eval_metric.threshold

        self._threshold = threshold

        # Try to import rouge_scorer
        try:
            from rouge_score import rouge_scorer
            self._rouge_scorer = rouge_scorer.RougeScorer(["rouge1"], use_stemmer=True)
        except ImportError:
            raise ImportError("rouge-score library is required for RougeEvaluator. "
                              "Install it with: pip install rouge-score")

    @staticmethod
    def get_metric_info() -> MetricInfo:
        """Get metadata information about this metric.

        Returns:
            MetricInfo object describing this metric
        """
        return MetricInfo(
            metric_name=PrebuiltMetrics.RESPONSE_MATCH_SCORE.value,
            description=("This metric compares the final response content between actual "
                         "and expected invocations using ROUGE-1 F1 score. ROUGE-1 measures "
                         "the overlap of unigrams (single words) between the two texts. "
                         "A score of 1.0 indicates perfect match, while 0.0 indicates no "
                         "overlap. Higher values are better."),
            metric_value_info=MetricValueInfo(interval=Interval(min_value=0.0, max_value=1.0)),
        )

    @override
    def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        """Evaluate response matching.

        Args:
            actual_invocations: Invocations from the agent
            expected_invocations: Expected invocations with reference responses

        Returns:
            Evaluation result with Rouge-1 scores

        Raises:
            ValueError: If expected_invocations is not provided
        """
        if expected_invocations is None:
            raise ValueError("expected_invocations is required for Rouge evaluation")

        per_invocation_results = []

        for actual, expected in zip(actual_invocations, expected_invocations):
            # Extract text from responses
            actual_text = self._get_text_from_content(actual.final_response)
            expected_text = self._get_text_from_content(expected.final_response)

            # Calculate Rouge-1 score
            rouge_scores = self._rouge_scorer.score(expected_text, actual_text)
            score = rouge_scores["rouge1"].fmeasure

            per_invocation_results.append(
                PerInvocationResult(actual_invocation=actual,
                                    expected_invocation=expected,
                                    score=score,
                                    eval_status=self._get_eval_status(score)))

        # Calculate overall score
        overall_score = statistics.mean([r.score for r in per_invocation_results])

        return EvaluationResult(
            overall_score=overall_score,
            overall_eval_status=self._get_eval_status(overall_score),
            per_invocation_results=per_invocation_results,
        )

    def _get_text_from_content(self, content: Optional[Content]) -> str:
        """Extract text from Content object.

        Args:
            content: The Content object

        Returns:
            Concatenated text from all parts
        """
        if content and content.parts:
            return "\n".join([part.text for part in content.parts if part.text])
        return ""

    def _get_eval_status(self, score: float) -> EvalStatus:
        """Get evaluation status based on score.

        Args:
            score: The computed score

        Returns:
            PASSED if score >= threshold, FAILED otherwise
        """
        return EvalStatus.PASSED if score >= self._threshold else EvalStatus.FAILED
