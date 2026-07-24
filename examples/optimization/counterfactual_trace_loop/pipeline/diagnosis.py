"""Evidence-first failure attribution and prompt-surface selection."""

from __future__ import annotations

from collections import Counter

from .models import CounterfactualEvidence, FailureAttribution

TARGETS = {
    "tool_selection_error": ["router_prompt"],
    "tool_parameter_error": ["skill_prompt"],
    "tool_sequence_error": ["skill_prompt"],
    "knowledge_recall_insufficient": ["skill_prompt"],
    "final_response_mismatch": ["system_prompt"],
    "format_violation": ["system_prompt"],
    "llm_rubric_not_met": ["system_prompt"],
    "compound_failure": ["router_prompt", "skill_prompt"],
}


class InfrastructureFailure(RuntimeError):
    """Explicit non-prompt failure raised by model/tool/backend adapters."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def attribute_from_evidence(
    case_id: str,
    evidence: list[CounterfactualEvidence],
    *,
    failure_reason: str = "",
    evaluations_used: int | None = None,
    budget: int = 7,
) -> FailureAttribution:
    """Infer category solely from intervention effects; reason is intentionally unused."""
    del failure_reason
    by_name = {item.intervention: item for item in evidence}
    single_map = {
        "replace_final_response": "final_response_mismatch",
        "replace_tool_name": "tool_selection_error",
        "replace_tool_arguments": "tool_parameter_error",
        "normalize_format": "format_violation",
    }
    repaired_singles = [
        (name, category)
        for name, category in single_map.items()
        if name in by_name and by_name[name].changed_fail_to_pass
    ]
    used = len(evidence) if evaluations_used is None else evaluations_used
    extra: list = []
    if repaired_singles:
        primary = repaired_singles[0][1]
        secondary = sorted({category for _, category in repaired_singles[1:]})
        coherent = all(by_name[name].semantically_coherent for name, _ in repaired_singles)
        confidence = (0.95 if len(repaired_singles) == 1 else 0.75) if coherent else 0.65
    else:
        repaired_combinations = [
            item
            for item in evidence
            if "+" in item.intervention or item.intervention == "replace_tool_name_and_arguments"
            if item.changed_fail_to_pass
        ]
        if repaired_combinations:
            coherent = all(item.semantically_coherent for item in repaired_combinations)
            primary, secondary, confidence = "compound_failure", [], 0.9 if coherent else 0.6
        else:
            metric_repairs = [item for item in evidence if item.repaired_metrics]
            if metric_repairs:
                first = metric_repairs[0]
                primary = single_map.get(first.intervention, "insufficient_evidence")
                secondary, confidence = [], 0.6
            else:
                statuses = {item.status for item in evidence}
                failed_metrics = {
                    name for item in evidence for name, score in item.before_metrics.items() if score < 1.0
                }
                if "tool_call_count_mismatch" in statuses:
                    primary, confidence = "tool_sequence_error", 0.85
                elif "llm_rubric_knowledge_recall" in failed_metrics:
                    primary, confidence = "knowledge_recall_insufficient", 0.8
                elif "llm_rubric_response" in failed_metrics:
                    primary, confidence = "llm_rubric_not_met", 0.8
                elif any("format" in name for name in failed_metrics):
                    primary, confidence = "format_violation", 0.8
                else:
                    primary, confidence = "insufficient_evidence", 0.0
                secondary = []
    if used >= budget and primary == "insufficient_evidence":
        extra.append("counterfactual_budget_exhausted")
    actionable = primary in TARGETS
    return FailureAttribution(
        case_id=case_id,
        failure_domain="agent_behavior_failure" if actionable else "evaluation_data_failure",
        primary_category=primary,
        secondary_categories=secondary,
        prompt_actionable=actionable,
        confidence=confidence,
        evidence=[*evidence, *extra],
        recommended_target_prompts=TARGETS.get(primary, []),
        evaluations_used=used,
    )


def classify_non_agent_failure(
    case_id: str,
    *,
    reliability: str = "trusted",
    issues: list[str] | None = None,
    error: Exception | None = None,
) -> FailureAttribution:
    issues = issues or []
    if isinstance(error, InfrastructureFailure):
        return FailureAttribution(case_id, "infrastructure_failure", error.category, evidence=[str(error)])
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return FailureAttribution(case_id, "infrastructure_failure", "model_timeout")
    if error is not None:
        return FailureAttribution(case_id, "evaluator_failure", "metric_execution_error", evidence=[str(error)])
    category = "missing_reference" if "missing_reference" in issues else "invalid_reference"
    if reliability == "suspect":
        category = "insufficient_evidence"
    return FailureAttribution(case_id, "evaluation_data_failure", category, evidence=issues)


def select_target_prompts(attributions: list[FailureAttribution]) -> list[str]:
    return sorted(
        {target for item in attributions if item.prompt_actionable for target in item.recommended_target_prompts}
    )


def build_failure_digest(attributions: list[FailureAttribution]) -> dict:
    """Separate prompt-actionable evidence from quarantined failure domains."""
    return {
        "actionable_failures": [item.to_dict() for item in attributions if item.prompt_actionable],
        "excluded_failures": [item.to_dict() for item in attributions if not item.prompt_actionable],
        "category_statistics": dict(Counter(item.primary_category for item in attributions)),
    }


import asyncio  # placed last to keep failure taxonomy prominent
