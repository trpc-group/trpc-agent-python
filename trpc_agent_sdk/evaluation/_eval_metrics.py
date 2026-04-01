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
"""Evaluation metrics definitions."""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Optional

from pydantic import Field
from pydantic import field_serializer

from ._common import EvalBaseModel
from ._llm_criterion import sanitize_criterion_for_export


class EvalStatus(Enum):
    """Status of evaluation."""
    PASSED = 1
    FAILED = 2
    NOT_EVALUATED = 3


class PrebuiltMetrics(Enum):
    """Pre-built evaluation metrics."""
    TOOL_TRAJECTORY_AVG_SCORE = "tool_trajectory_avg_score"
    """Average score for tool call trajectory matching."""

    FINAL_RESPONSE_AVG_SCORE = "final_response_avg_score"
    """Binary score for final response matching (criterion-based)."""

    RESPONSE_MATCH_SCORE = "response_match_score"
    """Score for response content matching using Rouge-1."""

    RESPONSE_EVALUATION_SCORE = "response_evaluation_score"
    """Overall response quality evaluation."""

    LLM_FINAL_RESPONSE = "llm_final_response"
    """LLM judge for final response (valid/invalid)."""

    LLM_RUBRIC_RESPONSE = "llm_rubric_response"
    """LLM rubric-based response quality."""

    LLM_RUBRIC_KNOWLEDGE_RECALL = "llm_rubric_knowledge_recall"
    """LLM rubric knowledge recall."""


class Interval(EvalBaseModel):
    """Represents a range of numeric values, e.g. [0, 1] or (2, 3) or [-1, 6)."""

    min_value: float = Field(description="The smaller end of the interval.")

    open_at_min: bool = Field(
        default=False,
        description=("The interval is Open on the min end. The default value is False, "
                     "which means that we assume that the interval is Closed."),
    )

    max_value: float = Field(description="The larger end of the interval.")

    open_at_max: bool = Field(
        default=False,
        description=("The interval is Open on the max end. The default value is False, "
                     "which means that we assume that the interval is Closed."),
    )


class MetricValueInfo(EvalBaseModel):
    """Information about the type of metric value."""

    interval: Optional[Interval] = Field(
        default=None,
        description="The values represented by the metric are of type interval.",
    )


class MetricInfo(EvalBaseModel):
    """Information about the metric that are used for Evals."""

    metric_name: str = Field(description="The name of the metric.")

    description: str = Field(default=None, description="A 2 to 3 line description of the metric.")

    metric_value_info: MetricValueInfo = Field(
        description="Information on the nature of values supported by the metric.")


class EvalMetric(EvalBaseModel):
    """Single evaluation metric: name, pass/fail threshold, optional criterion config.

    Attributes:
        metric_name: Name of the metric
        threshold: Threshold value for pass/fail
        criterion: Optional criterion config for evaluator
    """

    metric_name: str = Field(description="The name of the metric.")

    threshold: float = Field(description="Threshold value for this metric.")

    criterion: Optional[dict[str, Any]] = Field(
        default=None,
        description=("Optional. Keys: toolTrajectory/tool_trajectory, "
                     "finalResponse/final_response. Evaluator uses the key for its metric."),
    )

    @field_serializer("criterion")
    def _serialize_criterion(self, value: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """Strip api_key from nested llmJudge/judgeModel when exporting (avoid leaking secrets)."""
        return sanitize_criterion_for_export(value)
