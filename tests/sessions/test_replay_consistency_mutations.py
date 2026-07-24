"""End-to-end replay snapshot mutation tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from .replay_consistency.cases import replay_cases
from .replay_consistency.comparator import DiffEntry
from .replay_consistency.comparator import recursive_diff
from .replay_consistency.comparator import unallowed_diffs
from .replay_consistency.mutations import MUTATION_SECTION
from .replay_consistency.mutations import SUMMARY_REQUIRED_MUTATIONS
from .replay_consistency.mutations import mutate_snapshot
from .replay_consistency.mutations import mutations_for_case
from .replay_consistency.report import write_report
from .test_replay_consistency import _run_real_inmemory_snapshot


REQUIRED_REAL_MUTATIONS = {
    "drop_event",
    "reorder_events",
    "duplicate_event",
    "alter_event_text",
    "change_tool_args",
    "change_tool_response",
    "change_state_value",
    "drop_memory",
    "alter_memory_text",
    "drop_summary",
    "overwrite_summary_with_stale_text",
    "summary_wrong_session_id",
    "change_error_code",
    "drop_recovery_event",
}


def test_real_mutation_registry_covers_required_mutations():
    assert REQUIRED_REAL_MUTATIONS <= set(MUTATION_SECTION)
    seen_mutations = {mutation for case in replay_cases() for mutation in mutations_for_case(case)}
    assert REQUIRED_REAL_MUTATIONS <= seen_mutations


def _assert_diff_context(
    *,
    case_name: str,
    mutation: str,
    clean_snapshot: dict[str, Any],
    diffs: list[DiffEntry],
) -> None:
    unexpected = unallowed_diffs(diffs)
    assert unexpected, f"{case_name}/{mutation} was not detected"

    expected_section = MUTATION_SECTION[mutation]
    matching = [diff for diff in unexpected if diff.section == expected_section]
    assert matching, f"{case_name}/{mutation} did not report section {expected_section}"
    assert all(diff.session_id == clean_snapshot["session_id"] for diff in unexpected)
    assert all(diff.path for diff in unexpected)

    if expected_section == "events":
        assert any(diff.event_index is not None for diff in matching)
        if mutation == "change_tool_args":
            assert any("function_calls" in diff.path for diff in matching)
        if mutation == "change_tool_response":
            assert any("function_responses" in diff.path for diff in matching)
        if mutation == "alter_event_text":
            assert any(diff.path.endswith(".text") for diff in matching)
        if mutation == "change_error_code":
            assert any(diff.path.endswith(".error_code") for diff in matching)
        if mutation == "drop_recovery_event":
            assert any(diff.path.startswith("events[") and diff.right == "<missing>" for diff in matching)
    elif expected_section == "memories":
        assert any(diff.memory_index is not None or diff.section == "memories" for diff in matching)
    elif expected_section == "summary":
        assert any(diff.summary_id for diff in matching)
        if mutation == "drop_summary":
            assert any(diff.path == "summary" for diff in matching)
        elif mutation in {"overwrite_summary_with_stale_text", "overwrite_summary_text"}:
            assert any(diff.path == "summary.text" for diff in matching)
        elif mutation in {"summary_wrong_session_id", "wrong_summary_session"}:
            assert any("summary.metadata.session_id" in diff.path for diff in matching)


@pytest.mark.asyncio
async def test_real_replay_snapshot_mutation_detection_reports_precise_paths(tmp_path: Path):
    mutation_results: list[dict[str, Any]] = []
    for case in replay_cases():
        clean = await _run_real_inmemory_snapshot(tmp_path / f"real-mutation-{case.name}", case)
        for mutation in mutations_for_case(case):
            mutated = copy.deepcopy(clean)
            mutated["backend"] = "sqlite"
            mutate_snapshot(mutation, mutated)
            diffs = unallowed_diffs(recursive_diff(clean, mutated))
            _assert_diff_context(
                case_name=case.name,
                mutation=mutation,
                clean_snapshot=clean,
                diffs=diffs,
            )
            mutation_results.append(
                {
                    "case_name": case.name,
                    "mutation": mutation,
                    "detected": bool(unallowed_diffs(diffs)),
                    "diff_count": len(diffs),
                    "first_diff": diffs[0],
                    "diffs": diffs,
                }
            )

    report = write_report(
        tmp_path / "session_memory_summary_mutation_report.json",
        [],
        mutation_results=mutation_results,
    )
    assert report["schema_version"] == 1
    assert report["mutation_summary"]["mutation_count"] > 0
    assert report["mutation_summary"]["mutation_count"] == report["mutation_summary"]["detected_count"]
    assert report["mutation_summary"]["undetected_mutations"] == []
    assert any(
        diff.get("mutation") == "summary_wrong_session_id"
        and diff["path"] == "summary.metadata.session_id"
        for diff in report["unallowed_diffs"]
    )
    assert any(
        diff.get("mutation") in {"change_tool_args", "change_tool_response"}
        and diff["event_index"] is not None
        for diff in report["unallowed_diffs"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", SUMMARY_REQUIRED_MUTATIONS)
async def test_real_summary_required_mutations_detected(tmp_path: Path, mutation: str):
    case = next(case for case in replay_cases() if case.name == "summary_generation")
    clean = await _run_real_inmemory_snapshot(tmp_path / f"summary-required-{mutation}", case)
    mutated = copy.deepcopy(clean)
    mutated["backend"] = "sqlite"
    mutate_snapshot(mutation, mutated)
    diffs = unallowed_diffs(recursive_diff(clean, mutated))
    _assert_diff_context(
        case_name=case.name,
        mutation=mutation,
        clean_snapshot=clean,
        diffs=diffs,
    )
    assert all(diff.section == "summary" for diff in unallowed_diffs(diffs) if diff.path.startswith("summary"))
