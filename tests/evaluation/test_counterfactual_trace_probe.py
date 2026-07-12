# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Behavior tests for the counterfactual trace feasibility probe."""

from __future__ import annotations

from copy import deepcopy

import pytest

from trpc_agent_sdk.types import FunctionResponse

from examples.optimization.eval_optimize_loop.pipeline.interventions import (
    InterventionKind,
    build_counterfactual,
)
from examples.optimization.eval_optimize_loop.pipeline.probe import (
    build_probe_cases,
    evaluate_trace_cases,
    run_counterfactual_probe,
)


@pytest.mark.asyncio
async def test_official_metrics_react_to_counterfactual_trace_edits(tmp_path):
    """Final, tool-name, and compound edits must produce distinct metric deltas."""
    cases = build_probe_cases()
    baseline = await evaluate_trace_cases(cases, tmp_path)

    final_case = build_counterfactual(cases[0], InterventionKind.REPLACE_FINAL_RESPONSE)
    name_case = build_counterfactual(cases[1], InterventionKind.REPLACE_TOOL_NAME)
    compound_name = build_counterfactual(cases[2], InterventionKind.REPLACE_TOOL_NAME)
    compound_args = build_counterfactual(cases[2], InterventionKind.REPLACE_TOOL_ARGUMENTS)
    compound_both = build_counterfactual(cases[2], InterventionKind.REPLACE_TOOL_NAME_AND_ARGUMENTS)

    assert all(result.valid for result in (final_case, name_case, compound_name, compound_args, compound_both))

    changed = await evaluate_trace_cases(
        [
            final_case.eval_case,
            name_case.eval_case,
            compound_name.eval_case,
            compound_args.eval_case,
            compound_both.eval_case,
        ],
        tmp_path,
    )

    assert baseline["probe_final_response"]["final_response_avg_score"] == 0.0
    assert changed[final_case.eval_case.eval_id]["final_response_avg_score"] == 1.0

    assert baseline["probe_tool_name"]["tool_trajectory_avg_score"] == 0.0
    assert changed[name_case.eval_case.eval_id]["tool_trajectory_avg_score"] == 1.0

    assert changed[compound_name.eval_case.eval_id]["tool_trajectory_avg_score"] == 0.0
    assert changed[compound_args.eval_case.eval_id]["tool_trajectory_avg_score"] == 0.0
    assert changed[compound_both.eval_case.eval_id]["tool_trajectory_avg_score"] == 1.0


def test_interventions_are_deep_copied_and_report_invalid_tool_shapes():
    """An intervention must not mutate source traces and must explain invalid shapes."""
    case = build_probe_cases()[2]
    snapshot = deepcopy(case.model_dump(mode="json", by_alias=True))

    result = build_counterfactual(case, InterventionKind.REPLACE_TOOL_ARGUMENTS)

    assert result.valid is True
    assert case.model_dump(mode="json", by_alias=True) == snapshot

    missing_expected = case.model_copy(deep=True)
    missing_expected.conversation[0].intermediate_data.tool_uses = []
    missing = build_counterfactual(missing_expected, InterventionKind.REPLACE_TOOL_ARGUMENTS)
    assert missing.valid is False
    assert missing.status == "missing_expected_tool"

    mismatched = case.model_copy(deep=True)
    mismatched.actual_conversation[0].intermediate_data.tool_uses.append(
        mismatched.actual_conversation[0].intermediate_data.tool_uses[0].model_copy()
    )
    mismatch = build_counterfactual(mismatched, InterventionKind.REPLACE_TOOL_NAME)
    assert mismatch.valid is False
    assert mismatch.status == "tool_call_count_mismatch"

    invalid_arguments = case.model_copy(deep=True)
    invalid_arguments.actual_conversation[0].intermediate_data.tool_uses[0].args = "bad"
    bad_args = build_counterfactual(invalid_arguments, InterventionKind.REPLACE_TOOL_ARGUMENTS)
    assert bad_args.valid is False
    assert bad_args.status == "tool_arguments_not_dict"


@pytest.mark.asyncio
async def test_probe_report_records_metric_repairs_and_feasibility(tmp_path):
    """The persisted probe must contain auditable before/after evidence."""
    report = await run_counterfactual_probe(tmp_path)

    assert report["feasibility"]["supported"] is True
    assert report["source_trace_unchanged"] is True
    assert (tmp_path / "counterfactual_probe.json").is_file()
    assert (tmp_path / "counterfactual_probe.md").is_file()

    by_id = {item["case_id"]: item for item in report["cases"]}
    case_a = {item["intervention"]: item for item in by_id["probe_final_response"]["interventions"]}
    case_b = {item["intervention"]: item for item in by_id["probe_tool_name"]["interventions"]}
    case_c = {item["intervention"]: item for item in by_id["probe_compound_tool"]["interventions"]}

    assert case_a["replace_final_response"]["changed_fail_to_pass"] is True
    assert case_a["replace_final_response"]["repaired_metrics"] == ["final_response_avg_score"]
    assert case_b["replace_tool_name"]["repaired_metrics"] == ["tool_trajectory_avg_score"]
    assert case_c["replace_tool_name"]["changed_fail_to_pass"] is False
    assert case_c["replace_tool_arguments"]["changed_fail_to_pass"] is False
    assert case_c["replace_tool_name_and_arguments"]["changed_fail_to_pass"] is True


def test_tool_edit_with_original_tool_response_is_marked_incoherent():
    case = build_probe_cases()[1]
    case.actual_conversation[0].intermediate_data.tool_responses = [
        FunctionResponse.model_validate({"name": "get_invoice", "response": {"invoice_id": "R-102"}})
    ]

    result = build_counterfactual(case, InterventionKind.REPLACE_TOOL_NAME)

    assert result.structurally_valid is True
    assert result.semantically_coherent is False
    assert "tool_response_matches_original_call" in result.coherence_warnings


@pytest.mark.asyncio
async def test_probe_diagnosis_is_case_id_and_order_invariant(tmp_path):
    original = await run_counterfactual_probe(tmp_path / "original")
    renamed = build_probe_cases()
    for index, case in enumerate(renamed):
        case.eval_id = f"renamed_{index}"
    changed = await run_counterfactual_probe(tmp_path / "changed", list(reversed(renamed)))

    def signature(report):
        return sorted(
            (
                item["diagnosis"]["primary_category"],
                item["diagnosis"]["compound_failure"],
            )
            for item in report["cases"]
        )

    assert signature(original) == signature(changed)
    assert original["feasibility"]["supported"] is True
    assert changed["feasibility"]["supported"] is True
