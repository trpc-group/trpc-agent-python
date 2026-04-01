# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Collect evaluation results and print summary to terminal."""

from __future__ import annotations

import json
import os
import shutil
import statistics
from typing import Any
from typing import Optional

from trpc_agent_sdk.types import Content

from ._eval_case import IntermediateDataType
from ._eval_case import Invocation
from ._eval_case import get_all_tool_calls
from ._eval_metrics import EvalStatus
from ._eval_result import EvalCaseResult
from ._eval_result import EvalMetricResult
from ._eval_set import EvalSet

# Constants
DEFAULT_TERMINAL_WIDTH = 80
MIN_SECTION_HEADER_WIDTH = 100
VERTICAL_FIELD_INDENT = "    "
VERTICAL_FIELD_MAX_LEN = 500
VERTICAL_FIELD_PREFIX = "  "
INVOCATION_FIELD_INDENT = "  "
INVOCATION_FIELD_PREFIX = "    "
RESULT_LABELS = ("Agent Name", "Eval Set", "Overall Status", "Runs")


def _result_label_width() -> int:
    """Width for aligned result labels (Agent Name, Eval Set, etc.)."""
    return max(len(l) for l in RESULT_LABELS)


class MetricRunRecord:
    """One run's record for a metric: actual/expected invocation + metric result."""

    __slots__ = ("actual_invocation", "expected_invocation", "eval_metric_result")

    def __init__(
        self,
        *,
        actual_invocation: Invocation,
        expected_invocation: Invocation,
        eval_metric_result: EvalMetricResult,
    ):
        self.actual_invocation = actual_invocation
        self.expected_invocation = expected_invocation
        self.eval_metric_result = eval_metric_result


_EvalMetricResultWithInvocation = MetricRunRecord  # alias


class EvalResultHandler:
    """Handles evaluation result collection and terminal output."""

    def eval_status_str(self, status: EvalStatus) -> str:
        if status == EvalStatus.PASSED:
            return "passed"
        if status == EvalStatus.FAILED:
            return "failed"
        return "not_evaluated"

    def _terminal_width(self) -> int:
        try:
            cols = os.environ.get("COLUMNS")
            if cols is not None:
                w = int(cols)
                if w > 0:
                    return w
        except (ValueError, TypeError):
            pass
        try:
            return shutil.get_terminal_size().columns or DEFAULT_TERMINAL_WIDTH
        except Exception:
            return DEFAULT_TERMINAL_WIDTH

    def print_section_header(self, title: str) -> None:
        width = max(self._terminal_width(), MIN_SECTION_HEADER_WIDTH)
        fill_len = max(0, width - len(title) - 2)
        half = fill_len // 2
        line = "=" * half + " " + title + " " + "=" * (fill_len - half)
        print(f"\n{line}")

    def format_number_like_json(self, x: Optional[float]) -> str:
        if x is None:
            return "N/A"
        return json.dumps(x)

    def build_summary(
        self,
        eval_set: EvalSet,
        eval_results_by_eval_id: dict[str, list[EvalCaseResult]],
        agent_name: str,
        num_runs: int,
    ) -> dict[str, Any]:
        overall_status = "passed"
        eval_cases: list[dict[str, Any]] = []

        for eval_id, results_list in eval_results_by_eval_id.items():
            if not results_list:
                continue
            metric_agg: dict[str, list[tuple[Optional[float], float, EvalStatus]]] = {}
            for ecr in results_list:
                for emr in ecr.overall_eval_metric_results:
                    if emr.metric_name not in metric_agg:
                        metric_agg[emr.metric_name] = []
                    metric_agg[emr.metric_name].append((emr.score, emr.threshold, emr.eval_status))
            case_metric_results: list[dict[str, Any]] = []
            case_passed = True
            for metric_name, vals in metric_agg.items():
                scores = [v[0] for v in vals if v[0] is not None]
                threshold = vals[0][1] if vals else 0.0
                avg_score = statistics.mean(scores) if scores else None
                if avg_score is not None and avg_score >= threshold:
                    status = EvalStatus.PASSED
                elif avg_score is None:
                    status = EvalStatus.NOT_EVALUATED
                else:
                    status = EvalStatus.FAILED
                    case_passed = False
                case_metric_results.append({
                    "metric_name": metric_name,
                    "score": avg_score,
                    "threshold": threshold,
                    "eval_status": self.eval_status_str(status),
                })
            case_overall = "passed" if case_passed else "failed"
            if not case_passed:
                overall_status = "failed"
            eval_cases.append({
                "eval_case_id": eval_id,
                "overall_status": case_overall,
                "metric_results": case_metric_results,
            })

        eval_cases.sort(key=lambda c: c["eval_case_id"])

        return {
            "agent_name": agent_name,
            "eval_set_id": eval_set.eval_set_id,
            "overall_status": overall_status,
            "runs": num_runs,
            "eval_cases": eval_cases,
        }

    def summary_to_export_dict(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Convert summary to dict with camelCase keys for JSON export."""
        return {
            "agentName":
            summary["agent_name"],
            "evalSetId":
            summary["eval_set_id"],
            "overallStatus":
            summary["overall_status"],
            "runs":
            summary["runs"],
            "evalCases": [{
                "evalCaseId":
                c["eval_case_id"],
                "overallStatus":
                c["overall_status"],
                "metricResults": [{
                    "metricName": m["metric_name"],
                    "score": m["score"],
                    "threshold": m["threshold"],
                    "evalStatus": m["eval_status"],
                } for m in c["metric_results"]],
            } for c in summary["eval_cases"]],
        }

    def _convert_content_to_text(self, content: Optional[Content]) -> str:
        if content and content.parts:
            return "\n".join([part.text for part in content.parts if part.text])
        return ""

    def _convert_tool_calls_to_text(self, intermediate_data: Optional[IntermediateDataType]) -> str:
        tool_calls = get_all_tool_calls(intermediate_data)
        return "\n".join([str(t) for t in tool_calls])

    def _format_vertical_field(
        self,
        label: str,
        value: Optional[str],
        indent: str = VERTICAL_FIELD_INDENT,
        max_len: int = VERTICAL_FIELD_MAX_LEN,
        prefix: str = VERTICAL_FIELD_PREFIX,
    ) -> str:
        if not value:
            value = ""
        text = (value if len(value) <= max_len else value[:max_len].rstrip() + "\n... (truncated)")
        lines = text.splitlines()
        if not lines:
            return f"{prefix}{label}:"
        result = [f"{prefix}{label}: {lines[0]}"]
        for line in lines[1:]:
            result.append(prefix + indent + line)
        return "\n".join(result)

    def _format_invocation_field(self, label: str, value: Optional[str]) -> str:
        """Format a single field in an invocation block (fixed indent/prefix)."""
        return self._format_vertical_field(
            label,
            value,
            indent=INVOCATION_FIELD_INDENT,
            prefix=INVOCATION_FIELD_PREFIX,
        )

    def _invocation_block_lines(
        self,
        exp_inv: Invocation,
        act_inv: Invocation,
        invocation_id: str,
    ) -> list[str]:
        prompt = self._convert_content_to_text(exp_inv.user_content)
        expected_response = self._convert_content_to_text(exp_inv.final_response)
        actual_response = self._convert_content_to_text(act_inv.final_response)
        expected_tool_calls = self._convert_tool_calls_to_text(exp_inv.intermediate_data)
        actual_tool_calls = self._convert_tool_calls_to_text(act_inv.intermediate_data)
        return [
            f"    --- Invocation id: {invocation_id} ---",
            self._format_invocation_field("prompt", prompt),
            self._format_invocation_field("expected_response", expected_response),
            self._format_invocation_field("actual_response", actual_response),
            self._format_invocation_field("expected_tool_calls", expected_tool_calls),
            self._format_invocation_field("actual_tool_calls", actual_tool_calls),
            "",
        ]

    def process_metrics_and_get_failures(
        self,
        eval_metric_results: dict[str, list[MetricRunRecord]],
        print_detailed_results: bool,
        agent_module: str,
        eval_id: str = "",
        eval_set_id: str = "",
        details_sink: Optional[list[str]] = None,
    ) -> list[str]:
        failures = []

        if print_detailed_results and eval_metric_results and details_sink is not None:
            details_sink.append("")
            prefix = f"=== Eval Set: {eval_set_id} | " if eval_set_id else "=== "
            details_sink.append(f"{prefix}Case id: {eval_id} ===")

        for metric_name, eval_metric_results_with_invocations in eval_metric_results.items():
            if not eval_metric_results_with_invocations:
                continue

            threshold = eval_metric_results_with_invocations[0].eval_metric_result.threshold
            scores = [
                m.eval_metric_result.score for m in eval_metric_results_with_invocations
                if m.eval_metric_result.score is not None
            ]

            if scores:
                overall_score = statistics.mean(scores)
                overall_eval_status = (EvalStatus.PASSED if overall_score >= threshold else EvalStatus.FAILED)
            else:
                overall_score = None
                overall_eval_status = EvalStatus.NOT_EVALUATED

            if print_detailed_results and details_sink is not None:
                status_str = self.eval_status_str(overall_eval_status)
                score_str = self.format_number_like_json(overall_score)
                thresh_str = self.format_number_like_json(threshold)
                details_sink.append(f"  [Metric] {metric_name}: {status_str}, score {score_str} "
                                    f"(threshold {thresh_str})")

            if overall_eval_status != EvalStatus.PASSED:
                failures.append(f"{metric_name} for {agent_module} Failed. Expected {threshold}, "
                                f"but got {overall_score}.")

        if print_detailed_results and eval_metric_results and details_sink is not None:
            first_metric_list = next(iter(eval_metric_results.values()))
            num_runs = len(first_metric_list)
            for run_idx in range(num_runs):
                item = first_metric_list[run_idx]
                exp_inv = item.expected_invocation
                act_inv = item.actual_invocation
                raw_id = ((exp_inv.invocation_id if exp_inv.invocation_id else None)
                          or (act_inv.invocation_id if act_inv.invocation_id else None) or str(run_idx))
                invocation_id = (f"{raw_id} (run {run_idx + 1})" if num_runs > 1 else raw_id)
                details_sink.extend(self._invocation_block_lines(exp_inv, act_inv, invocation_id))

        return failures

    def _evaluation_result_header_lines(self, agent_name: str, num_runs: int) -> list[str]:
        """Return the 3 header lines: ✅ Evaluation completed, Agent Name, Runs."""
        w = _result_label_width()
        return [
            "✅ Evaluation completed",
            f"{'Agent Name':<{w}}: {agent_name}",
            f"{'Runs':<{w}}: {num_runs}",
        ]

    def build_evaluation_result_lines(
        self,
        summary: dict[str, Any],
        include_completed_line: bool = True,
        include_agent_runs: bool = True,
    ) -> list[str]:
        width = _result_label_width()
        lines = []
        header = self._evaluation_result_header_lines(summary["agent_name"], summary["runs"])
        if include_completed_line:
            lines.append(header[0])
        if include_agent_runs:
            lines.extend(header[1:3])
        lines.extend([
            f"{'Eval Set':<{width}}: {summary['eval_set_id']}",
            f"{'Overall Status':<{width}}: {summary['overall_status']}",
        ])
        for case in summary["eval_cases"]:
            lines.append(f"Case {case['eval_case_id']} -> {case['overall_status']}")
            for mr in case["metric_results"]:
                score_str = self.format_number_like_json(mr["score"])
                thresh_str = self.format_number_like_json(mr["threshold"])
                lines.append(f"  Metric {mr['metric_name']}: score {score_str} "
                             f"(threshold {thresh_str}) => {mr['eval_status']}")
            lines.append("")
        return lines

    def print_evaluation_result(self, summary: dict[str, Any]) -> None:
        self.print_section_header("Evaluation Result")
        print("")
        print("\n".join(self.build_evaluation_result_lines(summary)))

    def _print_blocks(self, blocks: list[list[str]]) -> None:
        """Print each block's lines with a blank line between blocks."""
        for i, block_lines in enumerate(blocks):
            if i > 0:
                print("")
            print("\n".join(block_lines))

    def _print_lines_blocks(self, blocks: list[tuple[str, list[str]]], section_title: str) -> None:
        """Print section header then each block's lines with blank line between."""
        if not blocks:
            return
        self.print_section_header(section_title)
        print("")
        self._print_blocks([lines for _, lines in blocks])

    def print_evaluation_report(
        self,
        all_details: list[tuple[str, list[str]]],
        all_results: list[tuple[str, list[str]]],
        display_agent_name: str,
        num_runs: int,
    ) -> None:
        """Print Execution Details and Evaluation Result sections (multi-evalset)."""
        self._print_lines_blocks(all_details, "Execution Details")
        if all_results:
            self.print_section_header("Evaluation Result")
            print("")
            print("\n".join(self._evaluation_result_header_lines(display_agent_name, num_runs)))
            print("")
            self._print_blocks([lines for _, lines in all_results])
