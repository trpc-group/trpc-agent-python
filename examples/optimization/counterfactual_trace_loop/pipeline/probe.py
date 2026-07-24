"""Minimal official-evaluator probe for counterfactual trace diagnosis."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from trpc_agent_sdk.evaluation import AgentEvaluator, EvalCase, EvalSet

from .diagnosis import attribute_from_evidence
from .interventions import InterventionKind, build_counterfactual
from .models import CounterfactualEvidence


def _content(text: str, role: str) -> dict:
    return {"role": role, "parts": [{"text": text}]}


def _invocation(
    invocation_id: str,
    user: str,
    response: str,
    tool_name: str | None = None,
    tool_args: dict | None = None,
) -> dict:
    invocation = {
        "invocation_id": invocation_id,
        "user_content": _content(user, "user"),
        "final_response": _content(response, "model"),
    }
    if tool_name is not None:
        invocation["intermediate_data"] = {
            "tool_uses": [{"id": f"{invocation_id}-tool", "name": tool_name, "args": tool_args or {}}]
        }
    return invocation


def build_probe_cases() -> list[EvalCase]:
    """Build three semantic traces without attribution labels or case metadata."""
    raw_cases = [
        {
            "eval_id": "probe_final_response",
            "eval_mode": "trace",
            "conversation": [_invocation("expected-a", "What is the refund status?", "Refund is pending.")],
            "actual_conversation": [_invocation("actual-a", "What is the refund status?", "Refund was denied.")],
        },
        {
            "eval_id": "probe_tool_name",
            "eval_mode": "trace",
            "conversation": [
                _invocation(
                    "expected-b",
                    "Find refund R-102.",
                    "Refund R-102 is pending.",
                    "get_refund",
                    {"refund_id": "R-102"},
                )
            ],
            "actual_conversation": [
                _invocation(
                    "actual-b",
                    "Find refund R-102.",
                    "Refund R-102 is pending.",
                    "get_invoice",
                    {"refund_id": "R-102"},
                )
            ],
        },
        {
            "eval_id": "probe_compound_tool",
            "eval_mode": "trace",
            "conversation": [
                _invocation(
                    "expected-c",
                    "Refund order O-77 because it is damaged.",
                    "Refund for O-77 was submitted.",
                    "create_refund",
                    {"order_id": "O-77", "reason": "damaged"},
                )
            ],
            "actual_conversation": [
                _invocation(
                    "actual-c",
                    "Refund order O-77 because it is damaged.",
                    "Refund for O-77 was submitted.",
                    "get_invoice",
                    {"invoice_id": "O-77"},
                )
            ],
        },
    ]
    return [EvalCase.model_validate(case) for case in raw_cases]


async def evaluate_trace_cases(cases: Iterable[EvalCase], workspace: Path) -> dict[str, dict[str, float]]:
    """Evaluate trace cases with the public executor and return per-case metric scores."""
    workspace.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    evalset_path = workspace / f"counterfactual-{run_id}.evalset.json"
    metrics_path = workspace / f"counterfactual-{run_id}.metrics.json"

    eval_set = EvalSet(eval_set_id=f"counterfactual_{run_id}", eval_cases=list(cases))
    evalset_path.write_text(eval_set.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    metrics_path.write_text(
        json.dumps(
            {
                "metrics": [
                    {"metric_name": "tool_trajectory_avg_score", "threshold": 1.0},
                    {"metric_name": "final_response_avg_score", "threshold": 1.0},
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    executor = AgentEvaluator.get_executer(
        os.path.relpath(evalset_path, Path.cwd()),
        eval_metrics_file_path_or_dir=os.path.relpath(metrics_path, Path.cwd()),
        print_detailed_results=False,
        print_summary_report=False,
    )
    try:
        await executor.evaluate()
    except Exception:
        # AgentEvaluator raises when one or more cases fail, after preserving
        # the EvaluateResult. Counterfactual diagnosis needs those failed
        # per-case metrics; genuine execution failures have no result.
        if executor.get_result() is None:
            raise
    result = executor.get_result()

    scores: dict[str, dict[str, float]] = {}
    for aggregate in result.results_by_eval_set_id.values():
        for eval_id, runs in aggregate.eval_results_by_eval_id.items():
            if not runs:
                continue
            scores[eval_id] = {
                metric.metric_name: float(metric.score or 0.0) for metric in runs[0].overall_eval_metric_results
            }
    evalset_path.unlink(missing_ok=True)
    metrics_path.unlink(missing_ok=True)
    return scores


def _passed(metrics: dict[str, float]) -> bool:
    return bool(metrics) and all(score >= 1.0 for score in metrics.values())


def _render_probe_markdown(report: dict) -> str:
    lines = [
        "# Counterfactual Trace Feasibility Probe",
        "",
        f"Supported: **{str(report['feasibility']['supported']).lower()}**",
        "",
        "The probe uses trace-mode `EvalCase` objects and the public "
        "`AgentEvaluator.get_executer()` API for baseline and counterfactual scoring.",
        "",
        "| Case | Intervention | Legal | Fail to pass | Repaired metrics | Unchanged metrics |",
        "|---|---|---:|---:|---|---|",
    ]
    for case in report["cases"]:
        for item in case["interventions"]:
            lines.append(
                "| {case} | {intervention} | {legal} | {fixed} | {repaired} | {unchanged} |".format(
                    case=case["case_id"],
                    intervention=item["intervention"],
                    legal=str(item["construction"]["valid"]).lower(),
                    fixed=str(item["changed_fail_to_pass"]).lower(),
                    repaired=", ".join(item["repaired_metrics"]) or "-",
                    unchanged=", ".join(item["unchanged_metrics"]) or "-",
                )
            )
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            report["feasibility"]["reason"],
            "",
        ]
    )
    return "\n".join(lines)


async def run_counterfactual_probe(output_dir: Path, cases: list[EvalCase] | None = None) -> dict:
    """Run and persist the A/B/C feasibility experiment."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = cases or build_probe_cases()
    originals = {case.eval_id: case.model_dump(mode="json", by_alias=True) for case in cases}
    baseline = await evaluate_trace_cases(cases, output_dir)

    built: dict[str, list] = {}
    valid_cases = []
    for case in cases:
        built[case.eval_id] = []
        for kind in InterventionKind:
            result = build_counterfactual(case, kind)
            built[case.eval_id].append(result)
            if result.valid and result.eval_case is not None:
                valid_cases.append(result.eval_case)
    changed = await evaluate_trace_cases(valid_cases, output_dir)

    case_reports = []
    for case in cases:
        before = baseline[case.eval_id]
        interventions = []
        for result in built[case.eval_id]:
            after = changed.get(result.eval_case.eval_id, {}) if result.eval_case else {}
            repaired = sorted(name for name, score in before.items() if score < 1.0 and after.get(name, score) >= 1.0)
            unchanged = sorted(name for name, score in before.items() if after.get(name) == score)
            interventions.append(
                {
                    "intervention": result.intervention.value,
                    "construction": {
                        "valid": result.valid,
                        "status": result.status,
                        "structurally_valid": result.structurally_valid,
                        "semantically_coherent": result.semantically_coherent,
                        "coherence_warnings": list(result.coherence_warnings),
                    },
                    "structurally_valid": result.structurally_valid,
                    "semantically_coherent": result.semantically_coherent,
                    "coherence_warnings": list(result.coherence_warnings),
                    "before_metrics": before,
                    "after_metrics": after,
                    "changed_fail_to_pass": (result.valid and not _passed(before) and _passed(after)),
                    "repaired_metrics": repaired,
                    "unchanged_metrics": unchanged,
                }
            )
        evidence = [
            CounterfactualEvidence(
                intervention=item["intervention"],
                valid=item["construction"]["valid"],
                status=item["construction"]["status"],
                changed_fail_to_pass=item["changed_fail_to_pass"],
                repaired_metrics=item["repaired_metrics"],
                unchanged_metrics=item["unchanged_metrics"],
                before_metrics=item["before_metrics"],
                after_metrics=item["after_metrics"],
                structurally_valid=item["structurally_valid"],
                semantically_coherent=item["semantically_coherent"],
                coherence_warnings=item["coherence_warnings"],
            )
            for item in interventions
        ]
        diagnosis = attribute_from_evidence(case.eval_id, evidence)
        case_reports.append(
            {
                "case_id": case.eval_id,
                "baseline_metrics": before,
                "baseline_passed": _passed(before),
                "interventions": interventions,
                "diagnosis": {
                    "primary_category": diagnosis.primary_category,
                    "secondary_categories": diagnosis.secondary_categories,
                    "compound_failure": diagnosis.primary_category == "compound_failure",
                    "confidence": diagnosis.confidence,
                },
            }
        )

    intervention_sets = [{item["intervention"]: item for item in case["interventions"]} for case in case_reports]
    supported = (
        any(items["replace_final_response"]["changed_fail_to_pass"] for items in intervention_sets)
        and any(items["replace_tool_name"]["changed_fail_to_pass"] for items in intervention_sets)
        and any(
            not items["replace_tool_name"]["changed_fail_to_pass"]
            and not items["replace_tool_arguments"]["changed_fail_to_pass"]
            and items["replace_tool_name_and_arguments"]["changed_fail_to_pass"]
            for items in intervention_sets
        )
    )
    source_unchanged = all(case.model_dump(mode="json", by_alias=True) == originals[case.eval_id] for case in cases)
    report = {
        "schema_version": "1.0",
        "probe": "trust-aware-counterfactual-trace-diagnosis",
        "official_api": "AgentEvaluator.get_executer",
        "feasibility": {
            "supported": supported,
            "reason": (
                "Official trace metrics distinguish final-response, tool-name, and "
                "tool-argument interventions; the compound case passes only after the "
                "combined tool-name-and-arguments intervention."
                if supported
                else "Official trace metrics did not produce the required explanatory deltas."
            ),
        },
        "source_trace_unchanged": source_unchanged,
        "cases": case_reports,
    }
    (output_dir / "counterfactual_probe.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "counterfactual_probe.md").write_text(_render_probe_markdown(report), encoding="utf-8")
    return report
