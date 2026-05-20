# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optimizer-facing wrapper around AgentEvaluator."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from statistics import mean
from typing import Optional

from ._agent_evaluator import AgentEvaluator
from ._agent_evaluator import _EvaluationCasesFailed
from ._eval_callbacks import Callbacks
from ._eval_metrics import EvalStatus
from ._eval_result import EvaluateResult
from ._remote_eval_service import CallAgent


@dataclass(frozen=True)
class EvaluationOutcome:
    """Summary metrics extracted from an EvaluateResult for the optimizer.

    Attributes:
        pass_rate: Fraction of cases whose final_eval_status is PASSED.
        tiebreaker: Mean of all per-case metric scores; used when pass_rate ties.
        metric_breakdown: Mean score per metric name across all cases.
        failed_case_ids: Eval ids of cases that did not pass; duplicated across runs.
        judge_model_calls: Currently always 0; the evaluator does not surface per-judge invocation counts.
        raw_result: The original EvaluateResult for downstream inspection.
    """

    pass_rate: float
    tiebreaker: float
    metric_breakdown: dict[str, float] = field(default_factory=dict)
    failed_case_ids: list[str] = field(default_factory=list)
    judge_model_calls: int = 0
    raw_result: Optional[EvaluateResult] = None


def summarize_outcome(result: EvaluateResult) -> EvaluationOutcome:
    """Reduce a raw EvaluateResult to the metrics the optimizer needs.

    judge_model_calls is set to 0 here; remote evaluators may overwrite it
    after the call returns when actual judge invocation counts are known.
    """
    total = 0
    passed = 0
    failed_case_ids: list[str] = []
    scores_by_metric: dict[str, list[float]] = {}

    for set_result in result.results_by_eval_set_id.values():
        for eval_id, runs in set_result.eval_results_by_eval_id.items():
            for run in runs:
                total += 1
                if run.final_eval_status == EvalStatus.PASSED:
                    passed += 1
                else:
                    failed_case_ids.append(eval_id)
                for metric in run.overall_eval_metric_results:
                    if metric.score is None:
                        continue
                    scores_by_metric.setdefault(metric.metric_name, []).append(metric.score)

    pass_rate = passed / total if total > 0 else 0.0
    metric_breakdown = {name: mean(scores) for name, scores in scores_by_metric.items()}
    all_scores = [s for scores in scores_by_metric.values() for s in scores]
    tiebreaker = mean(all_scores) if all_scores else 0.0

    return EvaluationOutcome(
        pass_rate=pass_rate,
        tiebreaker=tiebreaker,
        metric_breakdown=metric_breakdown,
        failed_case_ids=failed_case_ids,
        judge_model_calls=0,
        raw_result=result,
    )


async def run_evaluator(
    *,
    eval_dataset_path: str,
    eval_metrics_path: Optional[str],
    call_agent: CallAgent,
    callbacks: Optional[Callbacks],
    num_runs: int = 1,
    case_parallelism: Optional[int] = None,
) -> EvaluationOutcome:
    """Run the evaluator over a dataset and summarize the outcome.

    Args:
        eval_dataset_path: Path to an eval set file or directory of eval sets.
        eval_metrics_path: Path to a shared metrics config file; None falls back to dataset-local config.
        call_agent: Async function that maps a user query to an agent response.
        callbacks: Optional lifecycle callbacks passed through to the evaluator.
        num_runs: Number of runs per eval set.
        case_parallelism: Max concurrent cases for inference; None lets the
            evaluator use its default. Plumbs ``optimize.eval_case_parallelism``
            through to :meth:`AgentEvaluator.get_executer`.

    Returns:
        EvaluationOutcome with extracted pass_rate / tiebreaker / metric_breakdown / failed_case_ids.
    """
    executer = AgentEvaluator.get_executer(
        eval_dataset_path,
        call_agent=call_agent,
        callbacks=callbacks,
        num_runs=num_runs,
        print_detailed_results=False,
        print_summary_report=False,
        eval_result_output_dir=None,
        eval_metrics_file_path_or_dir=eval_metrics_path,
        case_parallelism=case_parallelism,
    )
    # _EvaluationCasesFailed signals "some cases failed" — the evaluator has
    # already populated ``executer.get_result()`` before raising, so we swallow
    # this specific subclass and let the optimizer keep iterating. Any other
    # exception (FileNotFoundError, network error, third-party AssertionError,
    # ...) is a real failure and must propagate: silently substituting an empty
    # EvaluateResult would make the optimizer see a 0.0 pass_rate and continue
    # optimizing against phantom data.
    try:
        await executer.evaluate()
    except _EvaluationCasesFailed:
        pass
    result = executer.get_result()
    if result is None:
        # _run raised before populating self._result. This only happens on a
        # real upstream error (which would have re-raised above) or a logic
        # bug. Return an empty outcome rather than crash, but the path is
        # defensive — not a normal control-flow branch.
        result = EvaluateResult()
    return summarize_outcome(result)
