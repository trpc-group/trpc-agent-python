"""Deep-copy counterfactual edits for trace-mode evaluation cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from trpc_agent_sdk.evaluation import EvalCase, get_all_tool_calls, get_all_tool_responses


class InterventionKind(str, Enum):
    """Supported minimal counterfactual interventions."""

    REPLACE_FINAL_RESPONSE = "replace_final_response"
    REPLACE_TOOL_NAME = "replace_tool_name"
    REPLACE_TOOL_ARGUMENTS = "replace_tool_arguments"
    REPLACE_TOOL_NAME_AND_ARGUMENTS = "replace_tool_name_and_arguments"
    REPLACE_TOOL_NAME_AND_FINAL_RESPONSE = "replace_tool_name+final_response"
    REPLACE_TOOL_ARGUMENTS_AND_FINAL_RESPONSE = "replace_tool_arguments+final_response"
    NORMALIZE_FORMAT = "normalize_format"


@dataclass(frozen=True)
class InterventionResult:
    """Construction result with an explicit validity status."""

    intervention: InterventionKind
    valid: bool
    status: str
    eval_case: Optional[EvalCase]
    structurally_valid: bool = True
    semantically_coherent: bool = True
    coherence_warnings: tuple[str, ...] = ()


def _invalid(kind: InterventionKind, status: str) -> InterventionResult:
    return InterventionResult(
        kind,
        False,
        status,
        None,
        structurally_valid=False,
        semantically_coherent=False,
        coherence_warnings=(status,),
    )


def build_counterfactual(case: EvalCase, kind: InterventionKind) -> InterventionResult:
    """Return a legal deep-copied trace case with only the requested fields edited."""
    if case.eval_mode != "trace" or not case.actual_conversation:
        return _invalid(kind, "missing_actual_trace")
    if not case.conversation:
        return _invalid(kind, "missing_expected_trace")
    if len(case.actual_conversation) != len(case.conversation):
        return _invalid(kind, "invocation_count_mismatch")

    changed = case.model_copy(deep=True)
    changed.eval_id = f"{case.eval_id}__{kind.value}"

    replace_final = kind in (
        InterventionKind.REPLACE_FINAL_RESPONSE,
        InterventionKind.REPLACE_TOOL_NAME_AND_FINAL_RESPONSE,
        InterventionKind.REPLACE_TOOL_ARGUMENTS_AND_FINAL_RESPONSE,
    )
    if replace_final:
        for actual, expected in zip(changed.actual_conversation, changed.conversation):
            if expected.final_response is None:
                return _invalid(kind, "missing_expected_final_response")
            actual.final_response = expected.final_response.model_copy(deep=True)
        if kind == InterventionKind.REPLACE_FINAL_RESPONSE:
            return InterventionResult(kind, True, "constructed", changed)

    if kind == InterventionKind.NORMALIZE_FORMAT:
        return _invalid(kind, "format_not_normalizable")

    coherence_warnings: list[str] = []
    for actual, expected in zip(changed.actual_conversation, changed.conversation):
        actual_tools = get_all_tool_calls(actual.intermediate_data)
        expected_tools = get_all_tool_calls(expected.intermediate_data)
        actual_responses = get_all_tool_responses(actual.intermediate_data)
        if not expected_tools:
            return _invalid(kind, "missing_expected_tool")
        if len(actual_tools) != len(expected_tools):
            return _invalid(kind, "tool_call_count_mismatch")
        for actual_tool, expected_tool in zip(actual_tools, expected_tools):
            if kind in (
                InterventionKind.REPLACE_TOOL_ARGUMENTS,
                InterventionKind.REPLACE_TOOL_NAME_AND_ARGUMENTS,
                InterventionKind.REPLACE_TOOL_ARGUMENTS_AND_FINAL_RESPONSE,
            ):
                if not isinstance(actual_tool.args, dict) or not isinstance(expected_tool.args, dict):
                    return _invalid(kind, "tool_arguments_not_dict")
                actual_tool.args = dict(expected_tool.args)
                if actual_responses:
                    coherence_warnings.append("tool_response_may_depend_on_original_arguments")
            if kind in (
                InterventionKind.REPLACE_TOOL_NAME,
                InterventionKind.REPLACE_TOOL_NAME_AND_ARGUMENTS,
                InterventionKind.REPLACE_TOOL_NAME_AND_FINAL_RESPONSE,
            ):
                actual_tool.name = expected_tool.name
        if actual_responses and kind in (
            InterventionKind.REPLACE_TOOL_NAME,
            InterventionKind.REPLACE_TOOL_NAME_AND_ARGUMENTS,
            InterventionKind.REPLACE_TOOL_NAME_AND_FINAL_RESPONSE,
        ):
            expected_names = {tool.name for tool in expected_tools}
            if any(response.name not in expected_names for response in actual_responses):
                coherence_warnings.append("tool_response_matches_original_call")

    warnings = tuple(sorted(set(coherence_warnings)))
    return InterventionResult(
        kind,
        True,
        "constructed",
        changed,
        structurally_valid=True,
        semantically_coherent=not warnings,
        coherence_warnings=warnings,
    )
