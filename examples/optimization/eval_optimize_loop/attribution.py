#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Evidence-based failure attribution for evaluation cases."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

CATEGORIES = (
    "reply_mismatch",
    "format_fail",
    "tool_call_error",
    "param_error",
    "rubric_fail",
    "knowledge_fail",
    "infrastructure_failure",
    "insufficient_evidence",
    "none",
)


@dataclass(frozen=True)
class AttributionInput:
    status: str
    metric_name_failed: Optional[str] = None
    metric_names_failed: tuple[str, ...] = ()
    metric_reasons: tuple[str, ...] = ()
    expected_text: Optional[str] = None
    actual_text: Optional[str] = None
    expected_tool_uses: tuple[dict[str, Any], ...] = ()
    actual_tool_uses: tuple[dict[str, Any], ...] = ()
    expected_tool_responses: tuple[dict[str, Any], ...] = ()
    actual_tool_responses: tuple[dict[str, Any], ...] = ()
    error_message: Optional[str] = None
    # Backward-compatible fields used by older callers/tests.
    scenario_tag: Optional[str] = None
    score: float = 1.0
    has_tool_calls_actual: bool = False
    has_tool_calls_expected: bool = False
    has_tool_param_mismatch: bool = False
    has_knowledge_response: bool = True


@dataclass(frozen=True)
class AttributionResult:
    category: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _tool_name(tool: dict[str, Any]) -> str:
    return str(tool.get("name") or "")


def _tool_args(tool: dict[str, Any]) -> dict[str, Any]:
    args = tool.get("args")
    return args if isinstance(args, dict) else {}


def _tool_mismatch(inp: AttributionInput) -> tuple[bool, bool]:
    expected = {_tool_name(tool): tool for tool in inp.expected_tool_uses if _tool_name(tool)}
    actual = {_tool_name(tool): tool for tool in inp.actual_tool_uses if _tool_name(tool)}
    if not expected:
        return False, False
    missing_or_wrong = bool(set(expected) - set(actual))
    parameter_error = any(
        _tool_args(expected[name]) != _tool_args(actual[name]) for name in set(expected) & set(actual)
    )
    return missing_or_wrong, parameter_error


def _parameter_differences(
    expected_tools: tuple[dict[str, Any], ...],
    actual_tools: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    expected = {_tool_name(tool): tool for tool in expected_tools if _tool_name(tool)}
    actual = {_tool_name(tool): tool for tool in actual_tools if _tool_name(tool)}
    return [
        {
            "tool_name": name,
            "expected_args": _tool_args(expected[name]),
            "actual_args": _tool_args(actual[name]),
        }
        for name in sorted(set(expected) & set(actual))
        if _tool_args(expected[name]) != _tool_args(actual[name])
    ]


def _failed_metric_names(inp: AttributionInput) -> tuple[str, ...]:
    names = list(inp.metric_names_failed)
    if inp.metric_name_failed and inp.metric_name_failed not in names:
        names.append(inp.metric_name_failed)
    return tuple(str(name) for name in names)


def classify_with_reason(inp: AttributionInput) -> AttributionResult:
    """Classify one failure without relying on scenario labels."""

    if inp.status == "PASSED":
        return AttributionResult("none", "case 已通过，无失败归因。")

    if inp.error_message or inp.status == "NOT_EVALUATED":
        reason = inp.error_message or "评测未完成，缺少可用的 agent 输出。"
        return AttributionResult(
            "infrastructure_failure",
            f"基础设施或评测执行失败：{reason}",
            {"error_message": inp.error_message, "status": inp.status},
        )

    metric_names = _failed_metric_names(inp)
    lowered_metrics = tuple(name.lower() for name in metric_names)
    reasons_text = " ".join(inp.metric_reasons).lower()

    if any("knowledge" in name for name in lowered_metrics) or any(
        marker in reasons_text for marker in ("knowledge", "recall", "知识", "召回")
    ):
        return AttributionResult(
            "knowledge_fail",
            "知识召回证据未达到评测要求。",
            {"failed_metrics": metric_names, "metric_reasons": inp.metric_reasons},
        )

    if any("rubric" in name for name in lowered_metrics):
        return AttributionResult(
            "rubric_fail",
            "LLM rubric 指标未达到阈值。",
            {"failed_metrics": metric_names, "metric_reasons": inp.metric_reasons},
        )

    expected_tools = inp.expected_tool_uses
    actual_tools = inp.actual_tool_uses
    if inp.has_tool_calls_expected and not expected_tools:
        expected_tools = ({"name": "<expected-tool>"},)
    if inp.has_tool_calls_actual and not actual_tools:
        actual_tools = ({"name": "<actual-tool>"},)
    normalized = AttributionInput(
        **{
            **inp.__dict__,
            "expected_tool_uses": expected_tools,
            "actual_tool_uses": actual_tools,
        }
    )
    missing_or_wrong, parameter_error = _tool_mismatch(normalized)
    if inp.has_tool_param_mismatch:
        parameter_error = True
    if parameter_error:
        return AttributionResult(
            "param_error",
            "工具名称匹配，但调用参数与期望轨迹不一致。",
            {
                "expected_tool_uses": expected_tools,
                "actual_tool_uses": actual_tools,
                "parameter_differences": _parameter_differences(expected_tools, actual_tools),
            },
        )
    if missing_or_wrong or (expected_tools and not actual_tools):
        return AttributionResult(
            "tool_call_error",
            "缺少期望工具调用，或调用了错误的工具。",
            {"expected_tool_uses": expected_tools, "actual_tool_uses": actual_tools},
        )

    if inp.expected_text is not None and inp.actual_text is not None:
        if _answer_numbers_match(inp.expected_text, inp.actual_text):
            return AttributionResult(
                "format_fail",
                "答案核心数值一致，但回复格式或必要结构不符合要求。",
                {"expected_text": inp.expected_text, "actual_text": inp.actual_text},
            )
        return AttributionResult(
            "reply_mismatch",
            "最终回复与参考答案不匹配。",
            {"expected_text": inp.expected_text, "actual_text": inp.actual_text},
        )

    return AttributionResult(
        "insufficient_evidence",
        "证据不足：缺少可核对的 actual/expected response 或工具轨迹，" "不能仅凭笼统 metric reason 可靠归因。",
        {
            "failed_metrics": metric_names,
            "metric_reasons": inp.metric_reasons,
            "status": inp.status,
        },
    )


def classify(inp: AttributionInput) -> str:
    """Backward-compatible category-only API."""

    return classify_with_reason(inp).category


def from_case_record(case: Any) -> AttributionResult:
    failed_metrics = tuple(
        str(metric.get("metric_name", "")) for metric in case.metric_results if metric.get("eval_status") != "PASSED"
    )
    metric_reasons = tuple(str(reason) for reason in case.failure_reasons if reason)
    return classify_with_reason(
        AttributionInput(
            status=case.challenger_status,
            metric_names_failed=failed_metrics,
            metric_reasons=metric_reasons,
            expected_text=case.expected_text,
            actual_text=case.actual_text,
            expected_tool_uses=tuple(case.expected_tool_uses),
            actual_tool_uses=tuple(case.actual_tool_uses),
            expected_tool_responses=tuple(case.expected_tool_responses),
            actual_tool_responses=tuple(case.actual_tool_responses),
            error_message=case.error_message,
        )
    )


def from_eval_case_result(
    result: Any,
    *,
    scenario_tag: Optional[str] = None,
    expected_text: Optional[str] = None,
) -> str:
    """Attribute directly from the public EvalCaseResult fields."""

    failed_metrics: list[str] = []
    reasons: list[str] = []
    for metric in result.overall_eval_metric_results or []:
        status = getattr(metric.eval_status, "name", str(metric.eval_status))
        if status != "PASSED":
            failed_metrics.append(metric.metric_name)
        details = getattr(metric, "details", None)
        reason = getattr(details, "reason", None) if details else None
        if reason:
            reasons.append(str(reason))

    actual_text: Optional[str] = None
    expected_tools: list[dict[str, Any]] = []
    actual_tools: list[dict[str, Any]] = []
    for per_invocation in result.eval_metric_result_per_invocation or []:
        actual = per_invocation.actual_invocation
        expected = per_invocation.expected_invocation
        if actual_text is None:
            actual_text = _content_to_text(getattr(actual, "final_response", None))
        actual_tools.extend(_intermediate_tools(actual))
        if expected is not None:
            expected_tools.extend(_intermediate_tools(expected))
            if expected_text is None:
                expected_text = _content_to_text(getattr(expected, "final_response", None))

    status = getattr(result.final_eval_status, "name", str(result.final_eval_status))
    return classify(
        AttributionInput(
            status=status,
            metric_names_failed=tuple(failed_metrics),
            metric_reasons=tuple(reasons),
            expected_text=expected_text,
            actual_text=actual_text,
            expected_tool_uses=tuple(expected_tools),
            actual_tool_uses=tuple(actual_tools),
            error_message=result.error_message,
        )
    )


def _content_to_text(content: Any) -> Optional[str]:
    parts = getattr(content, "parts", None) or []
    texts = [str(getattr(part, "text", "")) for part in parts if getattr(part, "text", None)]
    return "\n".join(texts) if texts else None


def _intermediate_tools(invocation: Any) -> list[dict[str, Any]]:
    intermediate = getattr(invocation, "intermediate_data", None)
    tools = getattr(intermediate, "tool_uses", None) or []
    return [
        (
            tool.model_dump(mode="json", by_alias=True)
            if hasattr(tool, "model_dump")
            else {"name": getattr(tool, "name", ""), "args": getattr(tool, "args", {})}
        )
        for tool in tools
    ]


def _answer_numbers_match(expected: str, actual: str) -> bool:
    expected_numbers = re.findall(r"-?\d+(?:\.\d+)?", expected)
    actual_numbers = re.findall(r"-?\d+(?:\.\d+)?", actual)
    if not expected_numbers or not actual_numbers:
        return False
    return expected_numbers[-1] == actual_numbers[-1]
