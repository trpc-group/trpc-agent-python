"""Budgeted counterfactual evaluation using the official evaluator."""

from __future__ import annotations

from pathlib import Path

from trpc_agent_sdk.evaluation import EvalCase

from .diagnosis import attribute_from_evidence
from .interventions import InterventionKind, build_counterfactual
from .models import CounterfactualEvidence, FailureAttribution
from .probe import evaluate_trace_cases


async def diagnose_trace_case(case: EvalCase, workspace: Path, max_evaluations: int = 7) -> FailureAttribution:
    baseline = (await evaluate_trace_cases([case], workspace))[case.eval_id]
    evidence = []
    singles = [
        InterventionKind.REPLACE_FINAL_RESPONSE,
        InterventionKind.REPLACE_TOOL_NAME,
        InterventionKind.REPLACE_TOOL_ARGUMENTS,
        InterventionKind.NORMALIZE_FORMAT,
    ]
    combinations = [
        InterventionKind.REPLACE_TOOL_NAME_AND_ARGUMENTS,
        InterventionKind.REPLACE_TOOL_NAME_AND_FINAL_RESPONSE,
        InterventionKind.REPLACE_TOOL_ARGUMENTS_AND_FINAL_RESPONSE,
    ]
    for kind in singles + combinations:
        if len(evidence) >= max_evaluations:
            break
        built = build_counterfactual(case, kind)
        if not built.valid or built.eval_case is None:
            evidence.append(
                CounterfactualEvidence(
                    kind.value,
                    False,
                    built.status,
                    False,
                    [],
                    [],
                    baseline,
                    {},
                    structurally_valid=built.structurally_valid,
                    semantically_coherent=built.semantically_coherent,
                    coherence_warnings=list(built.coherence_warnings),
                )
            )
            continue
        after = (await evaluate_trace_cases([built.eval_case], workspace))[built.eval_case.eval_id]
        repaired = sorted(k for k, v in baseline.items() if v < 1 and after.get(k, v) >= 1)
        unchanged = sorted(k for k, v in baseline.items() if after.get(k) == v)
        evidence.append(
            CounterfactualEvidence(
                kind.value,
                True,
                built.status,
                all(v >= 1 for v in after.values()),
                repaired,
                unchanged,
                baseline,
                after,
                structurally_valid=built.structurally_valid,
                semantically_coherent=built.semantically_coherent,
                coherence_warnings=list(built.coherence_warnings),
            )
        )
        if evidence[-1].changed_fail_to_pass and kind in singles:
            break
    return attribute_from_evidence(case.eval_id, evidence, evaluations_used=len(evidence), budget=max_evaluations)
