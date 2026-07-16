from __future__ import annotations

import re
from typing import Optional

from trpc_agent_sdk.evaluation import EvalCaseResult, EvalStatus

from .models import FailureAttribution, FailureCategory

_METRIC_TO_CATEGORY: dict[str, str] = {
    "final_response_avg_score": "final_response_mismatch",
    "llm_final_response": "llm_judge_not_passing",
    "llm_rubric_response": "llm_rubric_not_passing",
    "tool_trajectory_avg_score": "tool_trajectory_mismatch",
    "response_match_score": "response_match_below_threshold",
    "llm_rubric_knowledge_recall": "knowledge_recall_insufficient",
}

_FORMAT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("format_violation", re.compile(r"答案：")),
]


def _extract_actual_text(case_results: list[EvalCaseResult]) -> Optional[str]:
    for cr in case_results:
        for inv in cr.eval_metric_result_per_invocation:
            actual_inv = inv.actual_invocation
            if actual_inv and actual_inv.final_response and actual_inv.final_response.parts:
                parts = [p.text for p in actual_inv.final_response.parts if p.text]
                if parts:
                    return "".join(parts)
    return None


def _extract_expected_text(case_results: list[EvalCaseResult]) -> Optional[str]:
    for cr in case_results:
        for inv in cr.eval_metric_result_per_invocation:
            expected_inv = inv.expected_invocation
            if expected_inv and expected_inv.final_response and expected_inv.final_response.parts:
                parts = [p.text for p in expected_inv.final_response.parts if p.text]
                if parts:
                    return "".join(parts)
    return None


def attribute_failures(eval_results: dict[str, list[EvalCaseResult]]) -> FailureAttribution:
    categories: dict[str, FailureCategory] = {}
    total_cases = len(eval_results)
    failed_cases = 0

    for case_id, case_results in eval_results.items():
        last_result = case_results[-1] if case_results else None
        if last_result is None:
            continue

        if last_result.final_eval_status == EvalStatus.PASSED:
            continue

        failed_cases += 1

        for metric_result in last_result.overall_eval_metric_results:
            if metric_result.eval_status != EvalStatus.FAILED:
                continue

            cat = _METRIC_TO_CATEGORY.get(metric_result.metric_name, "unknown_metric_failure")
            if cat not in categories:
                categories[cat] = FailureCategory(count=0, case_ids=[])
            categories[cat].count += 1
            if case_id not in categories[cat].case_ids:
                categories[cat].case_ids.append(case_id)

        # Sub-classification: check for format violations in final_response_mismatch cases
        if "final_response_mismatch" in categories and case_id in categories["final_response_mismatch"].case_ids:
            actual_text = _extract_actual_text(case_results)
            expected_text = _extract_expected_text(case_results)

            if actual_text and expected_text:
                for sub_cat, pattern in _FORMAT_PATTERNS:
                    if pattern.search(expected_text) and not pattern.search(actual_text):
                        if sub_cat not in categories:
                            categories[sub_cat] = FailureCategory(count=0, case_ids=[])
                        categories[sub_cat].count += 1
                        if case_id not in categories[sub_cat].case_ids:
                            categories[sub_cat].case_ids.append(case_id)

    return FailureAttribution(
        total_cases=total_cases,
        failed_cases=failed_cases,
        categories=categories,
    )
