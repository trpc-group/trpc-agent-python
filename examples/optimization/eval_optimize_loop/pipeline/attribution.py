from __future__ import annotations

from typing import Any, Callable

from .models import CaseSnapshot, FailureAttribution, FailureType


def _rule(case: CaseSnapshot, failure_type: FailureType, evidence: str) -> FailureAttribution:
    return FailureAttribution(
        eval_id=case.eval_id,
        primary_type=failure_type,
        confidence=1.0,
        evidence=[evidence],
        source="rule",
    )


def _reasons(case: CaseSnapshot) -> list[str]:
    return [*case.execution_errors, *case.failure_reasons, *(reason for reasons in case.metric_reasons.values() for reason in reasons)]


def _judge_attribution(case: CaseSnapshot, judge: Callable[..., Any]) -> FailureAttribution | None:
    try:
        result = judge(case)
        if isinstance(result, FailureAttribution):
            candidate = result
        elif isinstance(result, dict):
            candidate = FailureAttribution.model_validate(
                {"eval_id": case.eval_id, "source": "judge", **result}
            )
        else:
            return None
    except Exception:
        return None
    if not candidate.evidence:
        return None
    return candidate.model_copy(update={"eval_id": case.eval_id, "source": "judge"})


def _tool_response_failure(case: CaseSnapshot) -> str | None:
    for response in case.tool_responses:
        value = response.response
        if value is None or value == {}:
            return f"tool response for {response.name or '<unnamed>'} was empty"
        if isinstance(value, dict):
            if value.get("error"):
                return f"tool response for {response.name or '<unnamed>'} contained an error: {value['error']}"
            if value.get("failed") is True:
                return f"tool response for {response.name or '<unnamed>'} was failed"
            status = value.get("status")
            if isinstance(status, str) and status.lower() in {"error", "failed"}:
                return f"tool response for {response.name or '<unnamed>'} had status {status}"
    return None


def attribute_case(case: CaseSnapshot, *, judge: Callable[..., Any] | None = None) -> FailureAttribution:
    """Classify a failed evaluation using deterministic evidence before any judge."""
    reasons = _reasons(case)
    reason_text = " ".join(reasons).lower()

    if case.execution_errors:
        timeout_error = next((error for error in case.execution_errors if "timeout" in error.lower()), None)
        if timeout_error is not None:
            return _rule(case, FailureType.TIMEOUT, timeout_error)
        return _rule(case, FailureType.EXECUTION_ERROR, case.execution_errors[0])

    if "timeout" in reason_text:
        return _rule(case, FailureType.TIMEOUT, next(reason for reason in reasons if "timeout" in reason.lower()))

    execution_markers = ("execution error", "connection refused", "connection reset", "request failed", "exception")
    if any(marker in reason_text for marker in execution_markers):
        return _rule(case, FailureType.EXECUTION_ERROR, next(reason for reason in reasons if any(marker in reason.lower() for marker in execution_markers)))

    actual_names = [call.name for call in case.tool_calls]
    expected_names = [call.name for call in case.expected_tool_calls]
    if actual_names != expected_names:
        return _rule(case, FailureType.TOOL_SELECTION_ERROR, f"actual tools={actual_names}; expected tools={expected_names}")

    if any(actual.arguments != expected.arguments for actual, expected in zip(case.tool_calls, case.expected_tool_calls)):
        return _rule(case, FailureType.TOOL_ARGUMENT_ERROR, "tool names matched but tool arguments differed")

    response_failure = _tool_response_failure(case)
    if response_failure is not None:
        return _rule(case, FailureType.TOOL_EXECUTION_ERROR, response_failure)

    tool_response_markers = ("tool response", "tool execution", "tool result")
    if any(marker in reason_text for marker in tool_response_markers) and any(
        marker in reason_text for marker in ("failed", "error", "empty")
    ):
        return _rule(case, FailureType.TOOL_EXECUTION_ERROR, next(reason for reason in reasons if any(marker in reason.lower() for marker in tool_response_markers)))

    format_markers = ("invalid json", "missing required field", "missing fake json", "fake json response")
    if any(marker in reason_text for marker in format_markers):
        return _rule(case, FailureType.FORMAT_VIOLATION, next(reason for reason in reasons if any(marker in reason.lower() for marker in format_markers)))

    failed_metrics = [name for name, passed in case.metric_passed.items() if not passed]
    if any("knowledge" in name.lower() for name in failed_metrics):
        return _rule(case, FailureType.KNOWLEDGE_RECALL_INSUFFICIENT, f"failed metrics={failed_metrics}")
    if any("rubric" in name.lower() for name in failed_metrics):
        return _rule(case, FailureType.LLM_RUBRIC_NOT_MET, f"failed metrics={failed_metrics}")
    if any("final_response" in name.lower() or "response_match" in name.lower() for name in failed_metrics):
        return _rule(case, FailureType.FINAL_RESPONSE_MISMATCH, f"failed metrics={failed_metrics}")

    if judge is not None:
        attribution = _judge_attribution(case, judge)
        if attribution is not None:
            return attribution
    return FailureAttribution(
        eval_id=case.eval_id,
        primary_type=FailureType.UNKNOWN,
        confidence=0.0,
        evidence=["no deterministic attribution rule matched"],
        source="fallback",
    )
