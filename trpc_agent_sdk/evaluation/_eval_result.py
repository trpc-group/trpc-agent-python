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
"""Evaluation results data structures."""

from __future__ import annotations

from typing import Any
from typing import Optional

from pydantic import Field

from ._common import EvalBaseModel
from ._eval_case import Invocation
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus


class PerInvocationResult(EvalBaseModel):
    """Result for a single invocation.

    Attributes:
        actual_invocation: The actual invocation from agent
        expected_invocation: The expected invocation (reference)
        score: The score for this invocation
        eval_status: The evaluation status
        reason: Optional reason from LLM judge (for LLM metrics).
        rubric_scores: Optional per-rubric scores (for rubric-based LLM metrics).
    """

    actual_invocation: Invocation
    expected_invocation: Optional[Invocation] = None
    score: Optional[float] = None
    eval_status: EvalStatus = EvalStatus.NOT_EVALUATED
    reason: Optional[str] = Field(default=None, description="Reason from judge (LLM metrics).")
    rubric_scores: Optional[list[Any]] = Field(
        default=None,
        description="Per-rubric scores (LLM rubric metrics). Use _llm_criterion.RubricScore.",
    )


class EvaluationResult(EvalBaseModel):
    """Result of evaluating multiple invocations.

    Attributes:
        overall_score: Overall score across all invocations
        overall_eval_status: Overall evaluation status
        per_invocation_results: Detailed results per invocation
    """

    overall_score: Optional[float] = None
    """Overall score based on each invocation."""

    overall_eval_status: EvalStatus = EvalStatus.NOT_EVALUATED
    """Overall status based on each invocation."""

    per_invocation_results: list[PerInvocationResult] = []
    """Detailed results per invocation."""


class EvalMetricResultDetails(EvalBaseModel):
    """Additional metric-specific info (reason, score, rubric_scores)."""

    reason: Optional[str] = Field(default=None, description="Reason for the metric result (e.g. LLM judge).")
    score: Optional[float] = Field(default=None, description="Score for this metric result.")
    rubric_scores: Optional[list[Any]] = Field(
        default=None,
        description="Per-rubric scores (LLM rubric metrics).",
    )


class EvalMetricResult(EvalMetric):
    """Result of evaluating a metric.

    Attributes:
        score: The computed score
        eval_status: Whether the evaluation passed or failed
        details: Nested details (reason, score, rubric_scores).
    """

    score: Optional[float] = Field(
        default=None,
        description="Score obtained after evaluating the metric.",
    )

    eval_status: EvalStatus = Field(description="The status of this evaluation.")

    details: Optional[EvalMetricResultDetails] = Field(
        default=None,
        description="Metric-specific details (reason, rubric_scores).",
    )


class EvalMetricResultPerInvocation(EvalBaseModel):
    """Evaluation metric results per invocation.

    Attributes:
        actual_invocation: The actual invocation
        expected_invocation: The expected invocation
        eval_metric_results: Results for each metric
    """

    actual_invocation: Invocation = Field(description="The actual invocation from the agent.")

    expected_invocation: Optional[Invocation] = Field(default=None, description="The expected invocation (reference).")

    eval_metric_results: list[EvalMetricResult] = Field(default_factory=list,
                                                        description="Evaluation results for each metric.")


class EvalCaseResult(EvalBaseModel):
    """Results for a single evaluation case.

    Attributes:
        eval_set_id: The eval set this case belongs to
        eval_id: The eval case identifier
        final_eval_status: Overall pass/fail status
        overall_eval_metric_results: Overall results per metric
        eval_metric_result_per_invocation: Detailed results per invocation
        session_id: Session ID used during evaluation
        user_id: User ID used during evaluation
    """

    eval_set_id: str = ""
    """The eval set id."""

    eval_id: str = ""
    """The eval case id."""

    run_id: Optional[int] = Field(
        default=None,
        description="1-based run index when num_runs > 1.",
    )

    final_eval_status: EvalStatus
    """Final eval status for this eval case."""

    error_message: Optional[str] = Field(
        default=None,
        description="Error message when evaluation execution failed.",
    )

    overall_eval_metric_results: list[EvalMetricResult]
    """Overall result for each metric."""

    eval_metric_result_per_invocation: list[EvalMetricResultPerInvocation]
    """Result for each metric on per invocation basis."""

    session_id: str
    """Session id used during evaluation."""

    user_id: Optional[str] = None
    """User id used during evaluation."""

    session_details: Optional[Any] = None
    """Session details generated during evaluation."""


class EvalStatusCounts(EvalBaseModel):
    """Counts of evaluation statuses."""

    passed: int = Field(default=0, description="Count of passed.")
    failed: int = Field(default=0, description="Count of failed.")
    not_evaluated: int = Field(default=0, description="Count of not evaluated.")


class EvalMetricRunSummary(EvalBaseModel):
    """Metric result in a single run."""

    metric_name: str = Field(description="Metric name.")
    score: float = Field(description="Score for this metric in the run.")
    eval_status: EvalStatus = Field(description="Eval status for this metric.")
    threshold: float = Field(description="Threshold used.")


class EvalMetricSummary(EvalBaseModel):
    """Metric summary across samples (e.g. across runs)."""

    metric_name: str = Field(description="Metric name.")
    average_score: float = Field(description="Averaged score across evaluated samples.")
    eval_status: EvalStatus = Field(description="Aggregated status from average and threshold.")
    threshold: float = Field(description="Threshold used.")
    status_counts: Optional[EvalStatusCounts] = None


class EvalCaseRunSummary(EvalBaseModel):
    """Single run of an eval case."""

    run_id: int = Field(description="1-based run id.")
    final_eval_status: EvalStatus = Field(description="Final status for this run.")
    error_message: Optional[str] = Field(default=None, description="Error if evaluation failed.")
    metric_results: list[EvalMetricRunSummary] = Field(
        default_factory=list,
        description="Metric outcomes for this run.",
    )


class EvalSetRunSummary(EvalBaseModel):
    """Summary of a single eval set run."""

    run_id: int = Field(description="1-based run id.")
    overall_status: EvalStatus = Field(description="Overall status for this run.")
    case_status_counts: Optional[EvalStatusCounts] = None
    metric_summaries: list[EvalMetricSummary] = Field(
        default_factory=list,
        description="Aggregated metric outcomes across cases in this run.",
    )


class EvalCaseResultSummary(EvalBaseModel):
    """Summary of a single eval case across runs."""

    eval_id: str = Field(description="Eval case id.")
    overall_status: EvalStatus = Field(description="Aggregated status across runs.")
    run_status_counts: Optional[EvalStatusCounts] = None
    metric_summaries: list[EvalMetricSummary] = Field(
        default_factory=list,
        description="Per-metric average score and status across runs.",
    )
    run_summaries: list[EvalCaseRunSummary] = Field(
        default_factory=list,
        description="Per-run summaries for this case.",
    )


class EvalSetResultSummary(EvalBaseModel):
    """Multi-run summary for an eval set result."""

    overall_status: EvalStatus = Field(description="Aggregated status across all cases and runs.")
    num_runs: int = Field(description="Number of runs.")
    run_status_counts: Optional[EvalStatusCounts] = None
    run_summaries: list[EvalSetRunSummary] = Field(default_factory=list)
    eval_case_summaries: list[EvalCaseResultSummary] = Field(default_factory=list)


class EvalSetResult(EvalBaseModel):
    """Results for an entire evaluation set.

    Attributes:
        eval_set_result_id: Unique identifier for this result
        eval_set_result_name: Human-readable name
        eval_set_id: The eval set that was evaluated
        eval_case_results: Results for each case in the set
        summary: Multi-run summary (when num_runs > 1 or multiple cases).
        creation_timestamp: When this result was created
    """

    eval_set_result_id: str
    eval_set_result_name: Optional[str] = None
    eval_set_id: str
    eval_case_results: list[EvalCaseResult] = Field(default_factory=list)
    summary: Optional[EvalSetResultSummary] = None
    creation_timestamp: float = 0.0


class EvalSetAggregateResult(EvalBaseModel):
    """Single eval set run result: per-case, per-run outcomes."""

    eval_results_by_eval_id: dict[str, list[EvalCaseResult]] = Field(default_factory=dict)
    num_runs: int = 1


class EvaluateResult(EvalBaseModel):
    """Aggregated evaluation result: one entry per eval set."""

    results_by_eval_set_id: dict[str, EvalSetAggregateResult] = Field(default_factory=dict)
