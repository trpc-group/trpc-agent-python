#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Trace replay, SDK evaluation, result normalization, and failure attribution."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalConfig
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import IntermediateData
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import get_all_tool_calls
from trpc_agent_sdk.evaluation import get_all_tool_responses

from .models import CaseEvaluation
from .models import FailureReason
from .models import MetricOutcome
from .models import SplitEvaluation
from .models import _validate_artifact_label

_SUPPORTED_SPLITS = {"train", "validation"}


def _text(content: Any) -> str:
    if content is None:
        return ""
    return "\n".join(str(getattr(part, "text", "") or "") for part in (content.parts or [])).strip()


def _payload_response_text(payload: dict[str, Any]) -> str:
    response = payload.get("final_response") or {}
    return "\n".join(str(part.get("text", "") or "") for part in response.get("parts", [])).strip()


def _tool_response_is_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status", "")).casefold()
    status_code = payload.get("status_code")
    numeric_status_code = None
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        numeric_status_code = status_code
    elif isinstance(status_code, str) and status_code.strip().isdigit():
        numeric_status_code = int(status_code.strip())
    return bool(
        payload.get("error") or status in {"error", "failed", "failure"} or payload.get("success") is False
        or payload.get("ok") is False or payload.get("is_error") is True
        or (numeric_status_code is not None and numeric_status_code >= 400))


class TraceEvaluator:
    """Replay deterministic traces through AgentEvaluator and explain failures."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    async def evaluate(
        self,
        eval_set: EvalSet,
        *,
        split: str,
        eval_config: EvalConfig,
        prompts: dict[str, str],
        trace_label: str,
    ) -> SplitEvaluation:
        """Materialize, persist, evaluate, and normalize one replay split."""
        if split not in _SUPPORTED_SPLITS:
            raise ValueError(f"unsupported replay split: {split!r}")
        trace_dir = self._trace_directory(trace_label)
        trace_set = self._materialize_trace(eval_set, prompts=prompts, trace_label=trace_label)
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / f"{split}.trace.evalset.json"
        trace_path.write_text(
            trace_set.model_dump_json(indent=2, by_alias=True) + "\n",
            encoding="utf-8",
        )
        _, _, _, raw_results = await AgentEvaluator.evaluate_eval_set(
            trace_set,
            eval_config=eval_config,
            num_runs=eval_config.num_runs,
            print_detailed_results=False,
            case_parallelism=1,
            case_eval_parallelism=1,
        )
        cases = [self._normalize_case(case, raw_results.get(case.eval_id, [])) for case in trace_set.eval_cases]
        pass_rate = sum(1 for case in cases if case.passed) / len(cases)
        average_score = sum(case.score for case in cases) / len(cases)
        return SplitEvaluation(
            split=split,
            pass_rate=pass_rate,
            average_score=average_score,
            cases=cases,
        )

    def _trace_directory(self, trace_label: str) -> Path:
        """Resolve a trace directory without allowing artifact-path escape."""
        _validate_artifact_label(trace_label, subject="trace label")
        output_root = self._output_dir.resolve()
        trace_root = (output_root / "traces").resolve()
        trace_dir = (trace_root / trace_label).resolve()
        try:
            trace_root.relative_to(output_root)
            trace_dir.relative_to(trace_root)
        except ValueError as error:
            raise ValueError("trace artifact path escapes the output directory") from error
        return trace_dir

    @staticmethod
    def _materialize_trace(
        eval_set: EvalSet,
        *,
        prompts: dict[str, str],
        trace_label: str,
    ) -> EvalSet:
        prompt_text = "\n".join(prompts.values())
        match = re.search(
            r"\[variant:\s*([a-zA-Z0-9_-]+)\]",
            prompt_text,
            re.IGNORECASE,
        )
        variant = match.group(1).lower() if match else "baseline"
        trace_cases: list[EvalCase] = []
        for case in eval_set.eval_cases:
            conversation = case.conversation or []
            if not conversation:
                raise ValueError(f"case {case.eval_id!r} has no expected conversation")
            state = case.session_input.state if case.session_input else {}
            payload = (state.get("variant_traces") or {}).get(variant)
            if not isinstance(payload, dict):
                raise ValueError(f"case {case.eval_id!r} has no replay trace for variant {variant!r}")
            expected_invocation = conversation[0]
            actual = Invocation(
                invocation_id=f"{trace_label}-{case.eval_id}",
                user_content=expected_invocation.user_content,
                final_response=payload.get("final_response"),
                intermediate_data=IntermediateData.model_validate(payload.get("intermediate_data") or {}),
            )
            trace_cases.append(
                EvalCase(
                    eval_id=case.eval_id,
                    eval_mode="trace",
                    conversation=case.conversation,
                    actual_conversation=[actual],
                    session_input=case.session_input,
                ))
        return EvalSet(
            eval_set_id=f"{eval_set.eval_set_id}_{trace_label}",
            name=eval_set.name,
            description=eval_set.description,
            eval_cases=trace_cases,
        )

    def _normalize_case(self, case: EvalCase, runs: list[EvalCaseResult]) -> CaseEvaluation:
        metric_values: dict[str, list[float]] = {}
        metric_templates: dict[str, Any] = {}
        for run in runs:
            for metric in run.overall_eval_metric_results or []:
                if metric.score is not None:
                    metric_values.setdefault(metric.metric_name, []).append(float(metric.score))
                metric_templates[metric.metric_name] = metric

        metrics: list[MetricOutcome] = []
        for name, metric in metric_templates.items():
            scores = metric_values.get(name, [])
            score = sum(scores) / len(scores) if scores else None
            passed = score is not None and score >= float(metric.threshold)
            reason = metric.details.reason if metric.details and metric.details.reason else ""
            metrics.append(
                MetricOutcome(
                    metric_name=name,
                    score=score,
                    threshold=float(metric.threshold),
                    passed=passed,
                    reason=reason,
                ))

        run_passed = bool(runs) and all(run.final_eval_status == EvalStatus.PASSED for run in runs)
        score_values = [metric.score for metric in metrics if metric.score is not None]
        score = sum(score_values) / len(score_values) if score_values else (1.0 if run_passed else 0.0)
        actual = ""
        key_trajectory = []
        if runs and runs[0].eval_metric_result_per_invocation:
            actual_invocation = runs[0].eval_metric_result_per_invocation[-1].actual_invocation
            actual = _text(actual_invocation.final_response)
            for tool_call in get_all_tool_calls(actual_invocation.intermediate_data):
                key_trajectory.append({
                    "kind": "tool_call",
                    "name": tool_call.name or "",
                    "payload": {
                        "id": tool_call.id,
                        "args": tool_call.args or {}
                    },
                })
            for tool_response in get_all_tool_responses(actual_invocation.intermediate_data):
                key_trajectory.append({
                    "kind": "tool_response",
                    "name": tool_response.name or "",
                    "payload": {
                        "id": tool_response.id,
                        "response": tool_response.response
                    },
                })
        failure_reasons = (self._attribute_failure(case, runs, metric_templates) if not run_passed else [])
        state = case.session_input.state if case.session_input else {}
        return CaseEvaluation(
            case_id=case.eval_id,
            passed=run_passed,
            score=score,
            key_case=bool(state.get("key_case", False)),
            hard_fail=bool(state.get("hard_fail", False)),
            metrics=metrics,
            primary_failure=failure_reasons[0].category if failure_reasons else None,
            failure_reasons=failure_reasons,
            actual_response=actual,
            expected_response=self._expected_response(case),
            key_trajectory=key_trajectory,
        )

    @staticmethod
    def _expected_response(case: EvalCase) -> str:
        conversation = case.conversation or []
        return _text(conversation[-1].final_response) if conversation else ""

    @staticmethod
    def _attribute_failure(
        case: EvalCase,
        runs: list[EvalCaseResult],
        metrics: dict[str, Any],
    ) -> list[FailureReason]:
        """Return evidence-backed reasons ordered from root cause to symptom."""
        if not runs or not runs[0].eval_metric_result_per_invocation:
            return [
                FailureReason(
                    category="other_metric_failure",
                    explanation="AgentEvaluator produced no invocation result.",
                    evidence={"case_id": case.eval_id},
                )
            ]
        invocation_result = runs[0].eval_metric_result_per_invocation[-1]
        invocation_metrics = {metric.metric_name: metric for metric in invocation_result.eval_metric_results or []}
        actual = invocation_result.actual_invocation
        expected = invocation_result.expected_invocation
        actual_calls = get_all_tool_calls(actual.intermediate_data)
        expected_calls = get_all_tool_calls(expected.intermediate_data if expected else None)
        actual_responses = get_all_tool_responses(actual.intermediate_data)
        reasons: list[FailureReason] = []

        all_metrics = dict(metrics)
        all_metrics.update(invocation_metrics)
        for metric_name, metric in all_metrics.items():
            if TraceEvaluator._metric_evaluated(metric):
                continue
            status = getattr(metric, "eval_status", EvalStatus.NOT_EVALUATED)
            details = getattr(metric, "details", None)
            reasons.append(
                FailureReason(
                    category="evaluation_error",
                    explanation="An evaluator metric produced no usable verdict.",
                    evidence={
                        "metric": metric_name,
                        "status": getattr(status, "name", str(status)),
                        "reason": details.reason if details and details.reason else "",
                    },
                ))

        error_responses = []
        for response in actual_responses:
            payload = response.response
            if _tool_response_is_error(payload):
                error_responses.append({
                    "tool": response.name,
                    "id": response.id,
                    "response": payload,
                })
        actual_names = [call.name for call in actual_calls]
        expected_names = [call.name for call in expected_calls]
        if error_responses:
            reasons.append(
                FailureReason(
                    category="tool_call_error",
                    explanation="A required tool returned an execution error.",
                    evidence={"tool_responses": error_responses},
                ))
        elif actual_names != expected_names:
            reasons.append(
                FailureReason(
                    category="tool_call_error",
                    explanation="The actual tool selection does not match the expected trajectory.",
                    evidence={
                        "actual_tools": actual_names,
                        "expected_tools": expected_names
                    },
                ))

        parameter_diffs = []
        if actual_names == expected_names:
            for actual_call, expected_call in zip(actual_calls, expected_calls):
                if (actual_call.args or {}) != (expected_call.args or {}):
                    parameter_diffs.append({
                        "tool": actual_call.name,
                        "actual": actual_call.args or {},
                        "expected": expected_call.args or {},
                    })
        if parameter_diffs:
            reasons.append(
                FailureReason(
                    category="parameter_error",
                    explanation="A tool was selected correctly but received different arguments.",
                    evidence={"differences": parameter_diffs},
                ))

        rubric_metric = invocation_metrics.get("llm_rubric_response") or metrics.get("llm_rubric_response")
        failed_rubrics = TraceEvaluator._failed_rubric_ids(rubric_metric)
        rubric_failed = (TraceEvaluator._metric_evaluated(rubric_metric)
                         and not TraceEvaluator._metric_passed(rubric_metric))
        rubric_scores_available = bool(rubric_metric and rubric_metric.details and rubric_metric.details.rubric_scores)
        format_rubric_status = TraceEvaluator._rubric_status(rubric_metric, "format")
        evidence_base = {
            "metric": "llm_rubric_response",
            "score": getattr(rubric_metric, "score", None),
            "threshold": getattr(rubric_metric, "threshold", None),
            "judge_reason":
            (rubric_metric.details.reason if rubric_metric is not None and rubric_metric.details else ""),
            "rubric_scores_available": rubric_scores_available,
            "failed_rubrics": failed_rubrics,
        }
        format_evidence: dict[str, Any] | None = None
        if format_rubric_status is False:
            format_evidence = {**evidence_base, "detector": "evaluator_rubric_id"}
        elif format_rubric_status is None and rubric_failed and expected is not None:
            # Some SDK aggregators omit per-rubric scores. Fall back to the
            # deterministic replay verdict, then to explicit request syntax.
            replay_signal, matched_variants = TraceEvaluator._replay_format_signal(case, _text(actual.final_response))
            violations = TraceEvaluator._format_violations(
                case,
                expected=_text(expected.final_response),
                actual=_text(actual.final_response),
            )
            if replay_signal is False:
                format_evidence = {
                    **evidence_base,
                    "detector": "replay_signal",
                    "format_pass": False,
                    "matched_variants": matched_variants,
                    "violations": violations,
                }
            elif replay_signal is None:
                if violations:
                    format_evidence = {
                        **evidence_base,
                        "detector": "requested_format",
                        "violations": violations,
                    }
        format_failed = format_evidence is not None
        if format_evidence is not None:
            reasons.append(
                FailureReason(
                    category="format_failure",
                    explanation="The format rubric failed for the final response.",
                    evidence=format_evidence,
                ))

        knowledge_metric = invocation_metrics.get("llm_rubric_knowledge_recall") or metrics.get(
            "llm_rubric_knowledge_recall")
        if (TraceEvaluator._metric_evaluated(knowledge_metric) and not TraceEvaluator._metric_passed(knowledge_metric)):
            reasons.append(
                FailureReason(
                    category="knowledge_recall_insufficiency",
                    explanation="Retrieved knowledge did not cover the facts required by the rubric.",
                    evidence={
                        "metric": "llm_rubric_knowledge_recall",
                        "score": knowledge_metric.score,
                        "threshold": knowledge_metric.threshold,
                    },
                ))

        quality_rubrics = [rubric_id for rubric_id in failed_rubrics if "format" not in rubric_id]
        if rubric_failed and not format_failed:
            reasons.append(
                FailureReason(
                    category="llm_rubric_failure",
                    explanation="The final answer failed one or more non-format quality rubrics.",
                    evidence={
                        "failed_rubrics": quality_rubrics,
                        "score": rubric_metric.score,
                        "threshold": rubric_metric.threshold,
                    },
                ))

        final_metric = metrics.get("final_response_avg_score")
        if TraceEvaluator._metric_evaluated(final_metric) and not TraceEvaluator._metric_passed(final_metric):
            reasons.append(
                FailureReason(
                    category="final_response_mismatch",
                    explanation="The final response does not contain the expected reference answer.",
                    evidence={
                        "actual": _text(actual.final_response),
                        "expected": _text(expected.final_response) if expected else "",
                    },
                ))

        trajectory_metric = metrics.get("tool_trajectory_avg_score")
        if (TraceEvaluator._metric_evaluated(trajectory_metric) and not TraceEvaluator._metric_passed(trajectory_metric)
                and not any(reason.category in {"tool_call_error", "parameter_error"} for reason in reasons)):
            reasons.append(
                FailureReason(
                    category="tool_call_error",
                    explanation="The tool trajectory metric failed without a narrower argument diagnosis.",
                    evidence={
                        "score": trajectory_metric.score,
                        "threshold": trajectory_metric.threshold,
                    },
                ))
        if not reasons:
            failed_metrics = [name for name, metric in metrics.items() if not TraceEvaluator._metric_passed(metric)]
            reasons.append(
                FailureReason(
                    category="other_metric_failure",
                    explanation="At least one evaluator metric failed without a specialized diagnosis.",
                    evidence={"failed_metrics": failed_metrics},
                ))
        return reasons

    @staticmethod
    def _metric_passed(metric: Any) -> bool:
        return metric.score is not None and float(metric.score) >= float(metric.threshold)

    @staticmethod
    def _metric_evaluated(metric: Any) -> bool:
        return (metric is not None and metric.score is not None
                and getattr(metric, "eval_status", EvalStatus.NOT_EVALUATED) != EvalStatus.NOT_EVALUATED)

    @staticmethod
    def _failed_rubric_ids(metric: Any) -> list[str]:
        if metric is None or metric.details is None:
            return []
        failed: list[str] = []
        for rubric in metric.details.rubric_scores or []:
            rubric_id = str(getattr(rubric, "id", "") or "").lower()
            score = getattr(rubric, "score", None)
            if score is not None and float(score) < 1.0:
                failed.append(rubric_id)
        return failed

    @staticmethod
    def _rubric_status(metric: Any, rubric_name: str) -> bool | None:
        if metric is None or metric.details is None:
            return None
        matching_scores = []
        for rubric in metric.details.rubric_scores or []:
            rubric_id = str(getattr(rubric, "id", "") or "").casefold()
            if rubric_name.casefold() not in rubric_id:
                continue
            score = getattr(rubric, "score", None)
            if score is not None:
                matching_scores.append(float(score))
        return all(score >= 1.0 for score in matching_scores) if matching_scores else None

    @staticmethod
    def _replay_format_signal(case: EvalCase, actual: str) -> tuple[bool | None, list[str]]:
        """Return an unambiguous offline format verdict for the replayed answer."""
        state = case.session_input.state if case.session_input else {}
        matches: dict[str, bool] = {}
        for variant, payload in (state.get("variant_traces") or {}).items():
            if not isinstance(payload, dict) or _payload_response_text(payload) != actual:
                continue
            signals = payload.get("signals") or {}
            value = signals.get("format_pass")
            if isinstance(value, bool):
                matches[str(variant)] = value
        verdicts = set(matches.values())
        verdict = next(iter(verdicts)) if len(verdicts) == 1 else None
        return verdict, sorted(matches)

    @staticmethod
    def _format_violations(
        case: EvalCase,
        *,
        expected: str,
        actual: str,
    ) -> list[str]:
        """Detect deterministic format mismatches requested by the evaluation case."""
        conversation = case.conversation or []
        request = _text(conversation[0].user_content).casefold() if conversation else ""
        violations: list[str] = []

        json_requested = TraceEvaluator._format_requested(
            request,
            positive_pattern=(r"(?:(?:只|仅)?(?:返回|输出|回复|回答|使用|采用|用|格式(?:为)?).{0,16}json|"
                              r"json.{0,12}(?:格式|对象|数组|返回|输出|回复|回答)|"
                              r"(?:return|output|respond|reply|use|provide).{0,16}json)"),
            format_pattern=r"json",
        )
        if json_requested:
            try:
                json.loads(actual)
            except (TypeError, ValueError, json.JSONDecodeError):
                violations.append("invalid_json")

        single_line_pattern = r"(?:一行|单行|single[ -]?line|one[ -]?line)"
        single_line_requested = TraceEvaluator._format_requested(
            request,
            positive_pattern=(r"(?:(?:只|仅)?(?:返回|输出|回复|回答).{0,12}(?:一行|单行)|"
                              r"(?:一行|单行).{0,8}(?:格式|输出|返回|回复)|"
                              r"(?:return|output|respond|reply).{0,12}(?:single|one)[ -]?line|"
                              r"(?:single|one)[ -]?line.{0,12}(?:format|output|response))"),
            format_pattern=single_line_pattern,
        )
        if single_line_requested and len(actual.splitlines()) != 1:
            violations.append("not_single_line")

        markdown_requested = TraceEvaluator._format_requested(
            request,
            positive_pattern=(r"(?:(?:返回|输出|回复|回答|使用|采用|用|格式(?:为)?).{0,16}markdown|"
                              r"markdown.{0,12}(?:格式|表格|列表|返回|输出|回复|回答)|"
                              r"(?:return|output|respond|reply|use|provide).{0,16}markdown)"),
            format_pattern=r"markdown",
        )
        if markdown_requested:
            missing = TraceEvaluator._markdown_structures(expected) - TraceEvaluator._markdown_structures(actual)
            violations.extend(f"missing_markdown_{item}" for item in sorted(missing))
        return violations

    @staticmethod
    def _format_requested(
        request: str,
        *,
        positive_pattern: str,
        format_pattern: str,
    ) -> bool:
        clauses = [item.strip() for item in re.split(r"[，。；;,.!?！？\n]+", request) if item.strip()]
        return any(
            re.search(positive_pattern, clause, re.IGNORECASE)
            and not TraceEvaluator._format_request_negated(clause, format_pattern) for clause in clauses)

    @staticmethod
    def _format_request_negated(clause: str, format_pattern: str) -> bool:
        chinese_prefix = r"(?:不要|无需|不必|不应|禁止|避免|请勿|切勿|不得|不能|不可)"
        chinese_action = r"(?:只|直接|再)?(?:返回|输出|回复|回答|使用|采用|用|以)"
        english_action = r"(?:return|output|respond|reply|use|provide)"
        patterns = [
            rf"{chinese_prefix}{chinese_action}.{{0,8}}{format_pattern}",
            rf"{chinese_prefix}\s*{format_pattern}",
            rf"(?:不用|不使用|不采用|不返回|不输出|不回复|不回答).{{0,8}}{format_pattern}",
            rf"(?:不是|非)\s*{format_pattern}(?:\s*格式)?",
            rf"(?:do\s+not|don't|not(?!\s+only)|never)\s+{english_action}.{{0,12}}{format_pattern}",
            rf"(?:without|avoid|no|never)\s+(?:{english_action}\s+)?{format_pattern}",
            rf"(?:not|no)\s+{format_pattern}(?:\s+format)?",
        ]
        return any(re.search(pattern, clause, re.IGNORECASE) for pattern in patterns)

    @staticmethod
    def _markdown_structures(text: str) -> set[str]:
        structures: set[str] = set()
        if re.search(r"(?m)^#{1,6}\s", text):
            structures.add("heading")
        if re.search(r"(?m)^(?:[-*+]\s|\d+[.)]\s)", text):
            structures.add("list")
        if re.search(r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", text):
            structures.add("table")
        if "```" in text:
            structures.add("fence")
        return structures
