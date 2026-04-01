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
"""Evaluation configuration."""

from __future__ import annotations

from typing import Any
from typing import Optional

from pydantic import Field

from ._common import EvalBaseModel
from ._eval_metrics import EvalMetric
from ._eval_metrics import PrebuiltMetrics


def _normalize_criterion_for_metric(metric_name: str, value: Any) -> Optional[dict]:
    """From criteria value object: return criterion dict or build from strategy alias."""
    if not isinstance(value, dict):
        return None
    criterion = value.get("criterion") or value.get("Criterion")
    if criterion is not None and isinstance(criterion, dict):
        return criterion
    strategy = value.get("strategy") or value.get("Strategy")
    if strategy is not None and isinstance(strategy, dict):
        if metric_name == PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value:
            return {"toolTrajectory": strategy}
        if metric_name == PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value:
            return {"finalResponse": strategy}
        return {"strategy": strategy}
    return None


def _threshold_from_value(value: Any) -> float:
    """Threshold from criteria value: number, or object with threshold / Threshold."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        t = value.get("threshold") or value.get("Threshold")
        if t is not None:
            return float(t)
    return 1.0


class EvalConfig(EvalBaseModel):
    """Evaluation config.

    Backward compatible: criteria value may be number (threshold only).
    """

    criteria: dict[str, Any] = Field(
        default_factory=dict,
        description=("Metric name -> threshold (number) or "
                     "{ threshold?, criterion? | strategy? }."),
    )
    metrics: Optional[list[Any]] = Field(
        default=None,
        description=("Optional metrics array; when set, used instead of criteria. "
                     "Item: metricName/metric_name, threshold, criterion."),
    )
    num_runs: int = Field(default=1, description="Number of runs per case.")
    user_simulator_config: Optional[Any] = Field(default=None, description="User simulator config.")

    def get_eval_metrics(self) -> list[EvalMetric]:
        """Build EvalMetric list from metrics (if set) or criteria.

        Accepts camelCase and snake_case keys.
        """
        if self.metrics:
            out = []
            for m in self.metrics:
                if not isinstance(m, dict):
                    continue
                name = m.get("metricName") or m.get("metric_name")
                if not name:
                    continue
                threshold = _threshold_from_value(m)
                criterion = m.get("criterion") or m.get("Criterion")
                if not isinstance(criterion, dict):
                    criterion = _normalize_criterion_for_metric(name, m)
                out.append(EvalMetric(metric_name=name, threshold=threshold, criterion=criterion))
            return out

        out = []
        for name, value in self.criteria.items():
            threshold = _threshold_from_value(value)
            criterion = _normalize_criterion_for_metric(name, value) if isinstance(value, dict) else None
            out.append(EvalMetric(metric_name=name, threshold=threshold, criterion=criterion))
        return out
