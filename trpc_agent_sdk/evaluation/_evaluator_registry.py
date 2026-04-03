# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
"""Evaluator registry for managing available metrics."""

from __future__ import annotations

from typing import Callable
from typing import Type

from ._final_response_evaluator import FinalResponseEvaluator
from ._llm_evaluator import LLMFinalResponseEvaluator
from ._llm_evaluator import LLMRubricKnowledgeRecallEvaluator
from ._llm_evaluator import LLMRubricResponseEvaluator
from ._rouge_evaluator import RougeEvaluator
from ._trajectory_evaluator import TrajectoryEvaluator
from ._eval_metrics import EvalMetric
from ._eval_metrics import PrebuiltMetrics
from ._evaluator_base import Evaluator


class EvaluatorRegistry:
    """Maps metric names to evaluator classes; provides get_evaluator(eval_metric).

    To customize the Compare rule in code (compare cannot be set from JSON),
    use set_criterion_compare(metric_name, callable) before get_evaluator is used.
    """

    def __init__(self):
        self._registry: dict[str, Type[Evaluator]] = {}
        self._criterion_compares: dict[str, Callable[..., bool]] = {}

        # Register built-in evaluators
        self.register(PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value, TrajectoryEvaluator)
        self.register(PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value, FinalResponseEvaluator)
        self.register(PrebuiltMetrics.RESPONSE_MATCH_SCORE.value, RougeEvaluator)
        self.register(PrebuiltMetrics.LLM_FINAL_RESPONSE.value, LLMFinalResponseEvaluator)
        self.register(PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value, LLMRubricResponseEvaluator)
        self.register(PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value, LLMRubricKnowledgeRecallEvaluator)

    def register(self, metric_name: str, evaluator_class: Type[Evaluator]) -> None:
        self._registry[metric_name] = evaluator_class

    def list_registered(self) -> list[str]:
        """Return sorted names of all registered metrics."""
        return sorted(self._registry.keys())

    def set_criterion_compare(
        self,
        metric_name: str,
        compare: Callable[..., bool],
    ) -> None:
        """Set a custom compare for a criterion-based metric (code only; not from JSON).

        For final_response_avg_score: compare(actual, expected) -> bool;
        actual/expected are final response content (Content-like or str).
        For tool_trajectory_avg_score: compare(actual_tool_calls, expected_tool_calls) -> bool;
        each is a list of tool calls (e.g. FunctionCall with .name, .args).

        Args:
            metric_name: e.g. final_response_avg_score or tool_trajectory_avg_score.
            compare: Callable that returns True when the pair is considered a match.
        """
        self._criterion_compares[metric_name] = compare

    def get_evaluator(self, eval_metric: EvalMetric) -> Evaluator:
        """Return evaluator instance for the given metric.

        Raises if metric not registered.
        """
        if eval_metric.metric_name not in self._registry:
            raise ValueError(f"No evaluator registered for metric: {eval_metric.metric_name}. "
                             f"Available metrics: {list(self._registry.keys())}")

        evaluator_class = self._registry[eval_metric.metric_name]
        evaluator = evaluator_class(eval_metric=eval_metric)

        compare_fn = self._criterion_compares.get(eval_metric.metric_name)
        if compare_fn is not None:
            if hasattr(evaluator, "_criterion") and getattr(evaluator, "_criterion") is not None:
                evaluator._criterion.compare = compare_fn
            if hasattr(evaluator, "_trajectory_criterion") and getattr(evaluator, "_trajectory_criterion") is not None:
                evaluator._trajectory_criterion.compare = compare_fn

        return evaluator


# Global default registry
EVALUATOR_REGISTRY = EvaluatorRegistry()
