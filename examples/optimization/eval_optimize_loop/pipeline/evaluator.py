# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Stage 1: Baseline evaluation using AgentEvaluator."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.evaluation import AgentEvaluator, EvalSet, EvalConfig
from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
from trpc_agent_sdk.evaluation._eval_case import get_all_tool_calls

from .config import BaselineResult, CaseResult, CaseMetricResult


def _extract_text(content) -> str:
    """Safely extract text from a Content object."""
    if content is None:
        return ""
    try:
        parts = content.parts if hasattr(content, "parts") else []
        return "".join(
            p.text for p in parts if hasattr(p, "text") and p.text
        )
    except Exception:
        return str(content)


def _extract_tool_call_dicts(intermediate_data) -> list[dict]:
    """Extract tool calls as plain dicts from intermediate_data."""
    calls = get_all_tool_calls(intermediate_data)
    return [{"name": c.name, "args": dict(c.args) if c.args else {}} for c in calls]


async def run_evaluation(
    evalset_path: str,
    eval_config_path: str | None = None,
    agent_module: str | None = None,
    call_agent=None,
    print_results: bool = False,
) -> BaselineResult:
    """Run evaluation on an evalset file and return structured BaselineResult.

    Args:
        evalset_path: Path to .evalset.json file or directory.
        eval_config_path: Optional path to test_config.json (per-dataset).
        agent_module: Module path for agent (e.g. "agent"). Required for
            live mode; ignored in trace mode.
        call_agent: Optional async callable (query: str) -> str for
            black-box / remote evaluation.
        print_results: If True, print detailed results to stdout.

    Returns:
        BaselineResult with pass_rate, metric_breakdown, and per_case details.
    """
    t0 = time.time()

    # Resolve shared config path
    kwargs: dict = {
        "eval_dataset_file_path_or_dir": evalset_path,
        "print_detailed_results": print_results,
    }
    if agent_module is not None:
        kwargs["agent_module"] = agent_module
    if call_agent is not None:
        kwargs["call_agent"] = call_agent
    if eval_config_path is not None:
        kwargs["eval_metrics_file_path_or_dir"] = eval_config_path

    executer = AgentEvaluator.get_executer(**kwargs)

    try:
        await executer.evaluate()
    except _EvaluationCasesFailed:
        pass  # Expected — result is populated even on failure

    result = executer.get_result()
    if result is None:
        raise RuntimeError("Evaluation returned no result.")

    # Walk the result tree
    per_case: list[CaseResult] = []
    metric_scores: dict[str, list[float]] = {}
    total_cases = 0
    passed_cases = 0
    eval_set_id = ""

    for sid, set_result in result.results_by_eval_set_id.items():
        eval_set_id = sid
        for cid, run_results in set_result.eval_results_by_eval_id.items():
            total_cases += 1
            all_pass = True
            case_metrics: dict[str, CaseMetricResult] = {}
            actual_tool_calls: list[dict] = []
            expected_tool_calls: list[dict] = []
            actual_response = ""
            expected_response = ""

            for ecr in run_results:
                if ecr.final_eval_status.name == "PASSED":
                    pass
                else:
                    all_pass = False

                # Per-invocation detail
                for pi in ecr.eval_metric_result_per_invocation:
                    actual_response = actual_response or _extract_text(
                        pi.actual_invocation.final_response
                    )
                    expected_response = expected_response or _extract_text(
                        pi.expected_invocation.final_response
                        if pi.expected_invocation
                        else None
                    )
                    actual_tool_calls = actual_tool_calls or _extract_tool_call_dicts(
                        pi.actual_invocation.intermediate_data
                    )
                    expected_tool_calls = expected_tool_calls or _extract_tool_call_dicts(
                        pi.expected_invocation.intermediate_data
                        if pi.expected_invocation
                        else None
                    )

                for emr in ecr.overall_eval_metric_results:
                    reason = ""
                    rubric_scores = []
                    if emr.details:
                        if emr.details.reason:
                            reason = emr.details.reason
                        if emr.details.rubric_scores:
                            rubric_scores = list(emr.details.rubric_scores)
                    cmr = CaseMetricResult(
                        metric_name=emr.metric_name,
                        score=emr.score or 0.0,
                        threshold=emr.threshold,
                        eval_status=emr.eval_status.name,
                        reason=reason,
                        rubric_scores=rubric_scores,
                    )
                    case_metrics[emr.metric_name] = cmr
                    metric_scores.setdefault(emr.metric_name, []).append(
                        emr.score or 0.0
                    )

            if all_pass:
                passed_cases += 1

            per_case.append(CaseResult(
                case_id=cid,
                overall_status="PASSED" if all_pass else "FAILED",
                metrics=case_metrics,
                actual_tool_calls=actual_tool_calls,
                expected_tool_calls=expected_tool_calls,
                actual_response=actual_response,
                expected_response=expected_response,
            ))

    pass_rate = passed_cases / max(total_cases, 1)
    metric_breakdown = {
        name: sum(scores) / max(len(scores), 1)
        for name, scores in metric_scores.items()
    }

    return BaselineResult(
        eval_set_id=eval_set_id,
        pass_rate=pass_rate,
        metric_breakdown=metric_breakdown,
        per_case=per_case,
    )
